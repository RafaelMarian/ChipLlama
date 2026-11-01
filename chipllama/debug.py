"""Reverse debugging: pasted register dump / debugger screenshot -> decoded bits -> likely root cause."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

from . import chipdb, prompts
from .models.base import ChatModel, Message, ModelError
from .timers import PRESCALERS, TIMERS, timer_period_s

_VAL = r"(0[xX][0-9a-fA-F]{1,4}|0[bB][01_]{1,24}|[0-9a-fA-F]{4}h?|[0-9a-fA-F]{4})"
_LINE = re.compile(r"\b([A-Za-z][A-Za-z0-9]{2,7})\s*(?:\(0[xX][0-9a-fA-F]+\))?\s*[:=\t ]\s*" + _VAL + r"\b")


def parse_dump(text: str) -> dict[str, int]:
    regs: dict[str, int] = {}
    known = set(chipdb.CORE_NAMES)
    for m in _LINE.finditer(text):
        name = m.group(1).upper()
        if name not in known:
            continue
        v = m.group(2).rstrip("hH").replace("_", "")
        try:
            if v.lower().startswith("0x"):
                val = int(v, 16)
            elif v.lower().startswith("0b"):
                val = int(v[2:], 2)
            else:
                val = int(v, 16)
        except ValueError:
            continue
        if val <= 0xFFFF:
            regs[name] = val
    return regs


def decode(regs: dict[str, int]) -> list[dict]:
    out = []
    for name, val in regs.items():
        if name in chipdb.REGISTERS:
            r = chipdb.REGISTERS[name]
            out.append({"register": name, "value": f"0x{val:04X}", "page": r.page,
                        "fields": {k: v for k, v in r.decode(val).items()},
                        "unimplemented_bits_set": f"0x{val & ~r.implemented_mask & 0xFFFF:04X}" if val & ~r.implemented_mask & 0xFFFF else None})
        else:
            out.append({"register": name, "value": f"0x{val:04X}", "page": None, "fields": {}, "unimplemented_bits_set": None})
    return out


@dataclass
class DebugFinding:
    severity: str
    code: str
    message: str

    def to_dict(self):
        return asdict(self)


def analyze(regs: dict[str, int], fosc_hz: float = 32e6, expected_period_s: float | None = None,
            expected_timer: int | None = None) -> tuple[list[DebugFinding], dict]:
    F: list[DebugFinding] = []
    info: dict = {"assumed_fosc_hz": fosc_hz, "timers": {}}
    fcy = fosc_hz / 2
    t32 = False
    if "T2CON" in regs and regs["T2CON"] & (1 << 3):
        t32 = True
        F.append(DebugFinding("info", "T32", "T2CON.T32=1: Timer2/3 form one 32-bit timer (PR3:PR2). T3CON is ignored and the Timer3 interrupt (T3IF/T3IE/T3IP) is used."))
    for n, t in TIMERS.items():
        con_n, pr_n = t["con"], t["pr"]
        if con_n not in regs:
            continue
        if n == 3 and t32:
            continue
        con = regs[con_n]
        d = chipdb.REGISTERS[con_n].decode(con)
        ton, tcs, tgate, tsidl = d["TON"], d["TCS"], d["TGATE"], d["TSIDL"]
        ps = PRESCALERS[d["TCKPS"]]
        tag = f"Timer{n}"
        if not ton:
            F.append(DebugFinding("error", f"{tag}_OFF", f"{con_n}.TON=0: {tag} is stopped, so it never counts or interrupts."))
        if tcs:
            F.append(DebugFinding("error", f"{tag}_TCS", f"{con_n}.TCS=1: clocked from the external pin, not Fosc/2. With no signal on the pin the timer does not advance."))
        if tgate and not tcs:
            F.append(DebugFinding("warning", f"{tag}_GATE", f"{con_n}.TGATE=1 with internal clock: counting only while the gate input is high."))
        if tsidl:
            F.append(DebugFinding("info", f"{tag}_SIDL", f"{con_n}.TSIDL=1: timer stops in Idle mode."))
        if val := regs[con_n] & ~chipdb.REGISTERS[con_n].implemented_mask & 0xFFFF:
            F.append(DebugFinding("warning", f"{tag}_UNIMPL", f"{con_n} shows unimplemented bits set (0x{val:04X}); they read 0 on real silicon, so this dump may be from the wrong register/width."))
        use_t32 = n == 2 and t32
        pr = regs.get(pr_n)
        if use_t32 and "PR3" in regs and pr is not None:
            pr = (regs["PR3"] << 16) | pr
        entry = {"prescale": ps, "TON": ton, "TCS": tcs}
        if pr is not None:
            per = timer_period_s(fcy, ps, pr)
            entry.update({"PR": pr, "period_s": per})
            F.append(DebugFinding("info", f"{tag}_PERIOD", f"{tag}: prescale 1:{ps}, PR={pr} -> period {per*1e3:.6g} ms at Fcy={fcy/1e6:g} MHz (assumed Fosc={fosc_hz/1e6:g} MHz)."))
            if pr == 0xFFFF and not use_t32:
                F.append(DebugFinding("warning", f"{tag}_PR_DEFAULT", f"{pr_n}=0xFFFF is the reset value - the period register was probably never loaded."))
            if expected_period_s and (expected_timer in (None, n)) and abs(per - expected_period_s) / expected_period_s > 0.01:
                F.append(DebugFinding("error", f"{tag}_MISMATCH", f"period {per*1e3:.6g} ms != expected {expected_period_s*1e3:.6g} ms. Check prescaler (TCKPS), PR and the assumed clock."))
        else:
            F.append(DebugFinding("info", f"{tag}_NOPR", f"{pr_n} not in the dump - cannot compute the period."))
        # interrupt side
        irq_n = 3 if use_t32 else n
        ti = TIMERS[irq_n]
        ifs_r, ifs_f = ti["ifs"]; iec_r, iec_f = ti["iec"]; ipc_r, ipc_f = ti["ipc"]
        ie = chipdb.REGISTERS[iec_r].field(iec_f); iff = chipdb.REGISTERS[ifs_r].field(ifs_f); ipf = chipdb.REGISTERS[ipc_r].field(ipc_f)
        if iec_r in regs and not (regs[iec_r] & ie.mask):
            F.append(DebugFinding("warning", f"{tag}_IE", f"{iec_r}.{iec_f}=0: interrupt disabled (fine for polling, but no ISR will run)."))
        if ipc_r in regs and ((regs[ipc_r] & ipf.mask) >> ipf.lsb) == 0:
            F.append(DebugFinding("error", f"{tag}_IP", f"{ipc_r}.{ipc_f}=0: priority 0 disables this interrupt source entirely."))
        if ifs_r in regs and (regs[ifs_r] & iff.mask):
            if iec_r in regs and not (regs[iec_r] & ie.mask):
                F.append(DebugFinding("info", f"{tag}_IF_SET", f"{ifs_r}.{ifs_f}=1 while {iec_f}=0: the timer HAS rolled over (flag latched) - the interrupt just isn't enabled."))
            else:
                F.append(DebugFinding("warning", f"{tag}_IF_STUCK", f"{ifs_r}.{ifs_f}=1 with interrupt enabled: flag pending. If this stays set the ISR is not clearing it, or CPU priority (SR.IPL) / DISI is blocking it."))
        info["timers"][f"Timer{n}"] = entry
    if not any(f.severity in ("error", "warning") for f in F):
        F.append(DebugFinding("info", "NO_FAULT", "No rule-based fault found in the supplied registers; check other peripherals/clock (OSCCON, config bits) or provide more registers."))
    return F, info


@dataclass
class DebugResult:
    registers: dict[str, int]
    decoded: list[dict]
    findings: list[DebugFinding]
    info: dict
    explanation: str = ""
    notes: list[str] = field(default_factory=list)
    transcribed_from_image: dict[str, str] | None = None

    def to_dict(self):
        return {"registers": {k: f"0x{v:04X}" for k, v in self.registers.items()}, "decoded": self.decoded,
                "findings": [f.to_dict() for f in self.findings], "info": self.info, "explanation": self.explanation,
                "notes": self.notes, "transcribed_from_image": self.transcribed_from_image}


def transcribe_images(model: ChatModel, images: list[bytes]) -> dict[str, int]:
    txt = model.complete([Message("user", prompts.VISION_TRANSCRIBE, images)])
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ModelError("vision transcription returned no JSON")
    j = json.loads(m.group(0))
    out = {}
    known = set(chipdb.CORE_NAMES)
    for k, v in j.items():
        k = k.upper().strip()
        try:
            val = int(str(v).strip().replace("_", ""), 0) if str(v).lower().startswith(("0x", "0b")) else int(str(v), 16)
        except ValueError:
            continue
        if k in known and 0 <= val <= 0xFFFF:
            out[k] = val
    return out


def debug_session(dump_text: str = "", images: list[bytes] | None = None, symptom: str = "", code: str = "",
                  model: ChatModel | None = None, fosc_hz: float = 32e6, expected_period_s: float | None = None,
                  expected_timer: int | None = None) -> DebugResult:
    notes: list[str] = []
    regs = parse_dump(dump_text or "")
    transcribed = None
    if images:
        if model is None:
            notes.append("screenshot given but no model configured: image ignored")
        else:
            try:
                t = transcribe_images(model, images)
                transcribed = {k: f"0x{v:04X}" for k, v in t.items()}
                for k, v in t.items():
                    regs.setdefault(k, v)   # typed dump wins over OCR
                notes.append(f"vision model transcribed {len(t)} register(s) from the screenshot - verify against the image")
            except (ModelError, ValueError) as e:
                notes.append(f"screenshot transcription failed: {e}")
    findings, info = analyze(regs, fosc_hz, expected_period_s, expected_timer)
    res = DebugResult(regs, decode(regs), findings, info, notes=notes, transcribed_from_image=transcribed)
    if not regs:
        res.notes.append("no known PIC24FJ256GB110 registers found in the input (expected lines like 'T1CON = 0x8030')")
        return res
    if model is not None:
        facts = "\n".join(f"- [{f.severity}] {f.message}" for f in findings)
        reg_lines = "\n".join(f"{d['register']} = {d['value']} {d['fields'] or ''}" for d in res.decoded)
        user = f"Symptom: {symptom or '(none given)'}\n\nDecoded registers:\n{reg_lines}\n\nRule-based findings:\n{facts}\n"
        if code:
            user += f"\nCode under suspicion:\n```c\n{code}\n```\n"
        try:
            res.explanation = model.complete([Message("system", prompts.DEBUG_SYSTEM), Message("user", user)])
        except ModelError as e:
            res.notes.append(f"model explanation unavailable: {e}")
    return res
