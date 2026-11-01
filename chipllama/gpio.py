"""GPIO: TRIS/PORT/LAT (DS39897C p.133), open-drain ODCx (p.134), change notification + pull-ups CNENx/CNPUx (p.135),
analog pin selection AD1PCFGL (p.273) and PPS output priority (p.136-138)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import chipdb
from .irq import vector, check_vector, prompt_line

PAGES = [133, 134, 135]
_PIN = r"\bR([A-G])(\d{1,2})\b"
_SKIP = re.compile(r"\b(tx|rx|uart|gate|gated|pwm|oc\d|ic\d|capture|adc|analog|sample|an\d+)\b", re.I)
_OUT = re.compile(r"\b(output|out|led|drive|drives|blink|toggle|toggles|high|low|set|clear|relay)\b", re.I)
_IN = re.compile(r"\b(input|in|button|switch|read|reads|sense|key|pushbutton)\b", re.I)
_PU = re.compile(r"pull[- ]?ups?\b", re.I)
_OD = re.compile(r"open[- ]?drain", re.I)
_CN = re.compile(r"change[- ]notification|\bCN\b|on[- ]change|pin[- ]change|change interrupt|\binterrupt\b", re.I)
_WORDS = re.compile(r"\b(gpio|led|button|switch|pull[- ]?up|open[- ]?drain|change notification|digital (in|out)put|"
                    r"blink|toggle)\b", re.I)


@dataclass
class PinCfg:
    name: str           # "RD6"
    output: bool
    pullup: bool = False
    od: bool = False
    cn: bool = False

    @property
    def port(self) -> str:
        return self.name[1]

    @property
    def bit(self) -> int:
        return int(self.name[2:])

    @property
    def cn_num(self) -> int | None:
        return chipdb.CN_PIN.get(self.name)

    @property
    def analog(self) -> int | None:
        return self.bit if self.port == "B" else None      # AN0..AN15 = RB0..RB15 on this family

    @property
    def rp(self) -> int | None:
        from .uart import PIN_RP
        return PIN_RP.get(self.name)

    def to_dict(self):
        return {"pin": self.name, "dir": "output" if self.output else "input", "pullup": self.pullup,
                "open_drain": self.od, "change_notification": self.cn, "cn": self.cn_num, "an": self.analog}


@dataclass
class GpioSpec:
    pins: list[PinCfg] = field(default_factory=list)
    cn_priority: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def cn_pins(self):
        return [p for p in self.pins if p.cn]

    def to_dict(self):
        return {"pins": [p.to_dict() for p in self.pins], "cn_priority": self.cn_priority}


def parse_request(text: str, fcy: float = 16e6, force: bool = False) -> GpioSpec | None:
    if not force and not _WORDS.search(text):
        return None
    spec = GpioSpec()
    byname: dict[str, PinCfg] = {}
    for clause in re.split(r"[,;.]|\band\b|\bthen\b|\bwhile\b", text):
        pins = [f"R{m.group(1).upper()}{int(m.group(2))}" for m in re.finditer(_PIN, clause, re.I)]
        if not pins or _SKIP.search(clause):
            continue
        is_in = bool(_IN.search(clause) or _PU.search(clause) or (_CN.search(clause) and not _OUT.search(clause)))
        is_out = bool(_OUT.search(clause)) and not re.search(r"\b(input|button|switch|read)\b", clause, re.I)
        for p in pins:
            c = byname.get(p)
            if c is None:
                c = byname[p] = PinCfg(p, output=is_out and not is_in)
                if not (is_in or is_out):
                    continue
            if _PU.search(clause):
                c.pullup, c.output = True, False
            if _OD.search(clause):
                c.od, c.output = True, True
            if _CN.search(clause) and not c.output:
                c.cn = True
            if is_in:
                c.output = False if not c.od else c.output
    m = re.search(r"priority\s*(\d)", text, re.I)
    for c in byname.values():
        if c.name not in chipdb.implemented_pins():
            spec.notes.append(f"{c.name} is not a pin on the PIC24FJ256GB110")
        spec.pins.append(c)
    if not spec.pins:
        return None if not force else spec
    if spec.cn_pins and m:
        spec.cn_priority = int(m.group(1))
    return spec


def _regs(p: PinCfg) -> dict:
    out = {"TRIS": (f"TRIS{p.port}", f"TRIS{p.port}{p.bit}", 0 if p.output else 1),
           "LAT" if p.output else "PORT": ((f"LAT{p.port}", f"LAT{p.port}{p.bit}") if p.output else
                                           (f"PORT{p.port}", f"R{p.port}{p.bit}"))}
    if p.od:
        out["ODC"] = (f"ODC{p.port}", f"OD{p.port}{p.bit}", 1)
    if p.analog is not None and not p.output:
        out["PCFG"] = ("AD1PCFGL", f"PCFG{p.analog}", 1)
    n = p.cn_num
    if p.pullup and n is not None:
        out["CNPU"] = (f"CNPU{n // 16 + 1}", f"CN{n}PUE", 1)
    if p.cn and n is not None:
        out["CNEN"] = (f"CNEN{n // 16 + 1}", f"CN{n}IE", 1)
    return out


def calc_lines(spec: GpioSpec):
    lines, d = [], {"pins": []}
    for p in spec.pins:
        r = _regs(p)
        desc = ("open-drain output" if p.od else "output") if p.output else "input" + (" + pull-up" if p.pullup else "")
        lines.append(f"{p.name}: {desc}" + (f", CN{p.cn_num}" if p.cn_num is not None else "")
                     + (f", AN{p.analog}" if p.analog is not None else "") + (f", RP{p.rp}" if p.rp is not None else ""))
        for k, v in r.items():
            if len(v) == 3:
                lines.append(f"  {v[0]}bits.{v[1]} = {v[2]}")
            else:
                lines.append(f"  {'write' if k == 'LAT' else 'read'} {v[0]}bits.{v[1]}")
        if p.cn and p.cn_num is None:
            lines.append(f"  !! {p.name} has no change-notification input")
        if p.pullup and p.cn_num is None:
            lines.append(f"  !! {p.name} has no internal pull-up (pull-ups exist only on CN pins)")
        if p.output and p.analog is not None:
            lines.append(f"  note: AN{p.analog} - digital output works, but PORT reads 0 unless PCFG{p.analog}=1")
        d["pins"].append(dict(p.to_dict(), regs={k: list(v) for k, v in r.items()}))
    if spec.cn_pins:
        v = vector("CNInterrupt")
        lines.append(prompt_line(v, spec.cn_priority))
        d["cn_vector"] = {"isr": v.isr, "ifs": v.ifs, "iec": v.iec, "ipc": v.ipc}
    lines += [f"  note: {n}" for n in spec.notes]
    return lines, d


def check(code: str, spec: GpioSpec, rep, emu) -> None:
    from .uart import rpor_field
    get = emu.get
    pins_ok = chipdb.implemented_pins()
    for p in spec.pins:
        if p.name not in pins_ok:
            rep.add("error", "GPIO_PIN", f"{p.name} does not exist on the PIC24FJ256GB110 (100-pin).")
            continue
        r = _regs(p)
        tr, tf, tv = r["TRIS"]
        if get(tr, tf) != tv:
            rep.add("error", "GPIO_TRIS", f"{tr}bits.{tf} must be {tv} ({'output' if tv == 0 else 'input'}) for {p.name}.",
                    tv, get(tr, tf))
        if p.analog is not None:
            pc = get("AD1PCFGL", f"PCFG{p.analog}")
            if not p.output and pc != 1:
                rep.add("error", "GPIO_ANALOG", f"{p.name} is AN{p.analog} and analog at reset: set AD1PCFGLbits.PCFG{p.analog}=1 "
                        "or the digital input always reads 0 (p.273).", 1, pc)
            elif p.output and pc != 1:
                rep.add("warning", "GPIO_ANALOG_OUT", f"{p.name} (AN{p.analog}) is still analog: the output drives, but "
                        f"reading back PORT{p.port} returns 0 (read-modify-write hazard). Prefer LAT or PCFG{p.analog}=1.")
        if p.output and p.rp is not None and p.rp <= 31:
            reg, f = rpor_field(p.rp)
            if get(reg, f):
                rep.add("error", "GPIO_PPS", f"{p.name}=RP{p.rp} has a remappable output assigned ({reg}.{f}={get(reg, f)}): "
                        "PPS outputs override LAT/TRIS (p.136).", 0, get(reg, f))
        if p.output:
            if any(e.target.split(".")[0] == f"PORT{p.port}" for e in emu.events):
                rep.add("warning", "GPIO_RMW", f"code writes PORT{p.port}; use LAT{p.port} for outputs to avoid "
                        "read-modify-write glitches (p.133).")
            odr, odf = f"ODC{p.port}", f"OD{p.port}{p.bit}"
            if p.od and get(odr, odf) != 1:
                rep.add("error", "GPIO_ODC", f"open-drain requested: set {odr}bits.{odf}=1 (p.134).", 1, get(odr, odf))
            elif not p.od and get(odr, odf) == 1:
                rep.add("warning", "GPIO_ODC", f"{odr}bits.{odf}=1 makes {p.name} open-drain (it cannot drive high).")
        else:
            if not re.search(rf"\bPORT{p.port}\b|\bPORT{p.port}bits\.R{p.port}{p.bit}\b|\b_R{p.port}{p.bit}\b", code):
                rep.add("warning", "GPIO_READ", f"{p.name} is an input but PORT{p.port} is never read "
                        f"(read PORT{p.port}bits.R{p.port}{p.bit}; LAT returns the latch, not the pin).")
        n = p.cn_num
        if p.pullup:
            if n is None:
                rep.add("error", "GPIO_NO_PU", f"{p.name} has no internal pull-up (only CN pins have one): use an external resistor.")
            elif get(f"CNPU{n // 16 + 1}", f"CN{n}PUE") != 1:
                rep.add("error", "GPIO_PU", f"pull-up for {p.name} is CN{n}: set CNPU{n // 16 + 1}bits.CN{n}PUE=1 (p.135).",
                        1, get(f"CNPU{n // 16 + 1}", f"CN{n}PUE"))
        if p.cn:
            if n is None:
                rep.add("error", "GPIO_NO_CN", f"{p.name} has no change-notification input; pick a CNx pin.")
            elif get(f"CNEN{n // 16 + 1}", f"CN{n}IE") != 1:
                rep.add("error", "GPIO_CNEN", f"{p.name} is CN{n}: set CNEN{n // 16 + 1}bits.CN{n}IE=1 (p.135).",
                        1, get(f"CNEN{n // 16 + 1}", f"CN{n}IE"))
    if spec.cn_pins:
        check_vector(emu, rep, vector("CNInterrupt"), priority=spec.cn_priority, what="change-notification")


def prompt_text(spec: GpioSpec) -> str:
    lines, _ = calc_lines(spec)
    return ("GPIO facts (DS39897C p.133-135; verified later): TRISx bit 0 = output, 1 = input; write outputs via LATx, "
            "read inputs via PORTx; RB0..RB15 are AN0..AN15 and power up ANALOG (AD1PCFGL bit = 1 makes them digital); "
            "pull-ups and change notification exist only on CNx pins (CNPUx/CNENx); a remappable output (RPORx) "
            "overrides the port.\n" + "\n".join(lines))


def pages(spec: GpioSpec) -> list[int]:
    out = list(PAGES)
    if any(p.analog is not None for p in spec.pins):
        out.append(273)
    if spec.cn_pins:
        out += vector("CNInterrupt").pages[:2]
    return list(dict.fromkeys(out))


FINDING_PAGES = {"GPIO_TRIS": [133], "GPIO_ODC": [134], "GPIO_PU": [135], "GPIO_CNEN": [135], "GPIO_NO_": [135],
                 "GPIO_ANALOG": [273], "GPIO_PPS": [136], "GPIO_RMW": [133], "GPIO_READ": [133], "GPIO_PIN": []}
