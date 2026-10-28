"""Deterministic register checker: does this C init code really configure the timer as requested?"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from . import chipdb
from . import board as boardmod
from .cemu import Emulation, emulate, isr_clears_flag, collect_defines, strip_comments, eval_expr, EvalError
from .timers import PRESCALERS, TIMERS, TimerSpec, TimerSolution, solve, timer_period_s, interrupt_names


@dataclass
class Finding:
    severity: str          # "error" | "warning" | "info"
    code: str
    message: str
    expected: str | None = None
    actual: str | None = None

    def to_dict(self):
        return asdict(self)


@dataclass
class CheckReport:
    findings: list[Finding] = field(default_factory=list)
    registers: dict[str, int] = field(default_factory=dict)       # final register values from the code
    expected: dict[str, int] = field(default_factory=dict)        # reference values
    achieved_period_s: float | None = None
    target_period_s: float | None = None
    reference: dict | None = None

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == "error"]

    def add(self, sev, code, msg, expected=None, actual=None):
        self.findings.append(Finding(sev, code, msg, None if expected is None else str(expected),
                                     None if actual is None else str(actual)))

    def to_dict(self):
        return {"ok": self.ok, "findings": [f.to_dict() for f in self.findings],
                "registers": {k: f"0x{v:04X}" for k, v in self.registers.items()},
                "expected": {k: f"0x{v:04X}" for k, v in self.expected.items()},
                "achieved_period_s": self.achieved_period_s, "target_period_s": self.target_period_s,
                "reference": self.reference}

    def feedback_text(self) -> str:
        """Compact text fed back to the model for self-correction."""
        lines = []
        for f in self.findings:
            if f.severity in ("error", "warning"):
                s = f"[{f.severity.upper()}:{f.code}] {f.message}"
                if f.expected is not None:
                    s += f" (expected {f.expected}, code gives {f.actual})"
                lines.append(s)
        return "\n".join(lines)


def check_timer(code: str, spec: TimerSpec, emu: Emulation | None = None, *,
                profile: "boardmod.BoardProfile | None" = None, ics: str = "PGx2",
                require_config: bool = True) -> CheckReport:
    rep = CheckReport()
    emu = emu or emulate(code)
    try:
        ref = solve(spec)
        rep.reference = ref.to_dict()
        rep.expected = dict(ref.registers)
        rep.target_period_s = spec.period_s
    except ValueError as e:
        rep.add("error", "SPEC", f"request cannot be satisfied by this timer: {e}")
        return rep
    n = 2 if spec.bits32 else spec.timer
    t = TIMERS[n]
    con_name, pr_name = t["con"], t["pr"]
    ints = interrupt_names(spec)
    con = chipdb.REGISTERS[con_name]
    regs = emu.regs
    for r in (con_name, pr_name, "PR3" if spec.bits32 else None, ints["ifs"][0], ints["iec"][0], ints["ipc"][0]):
        if r:
            rep.registers[r] = regs.get(r, 0)

    wrote = {e.target.split(".")[0] for e in emu.events}
    fld = lambda name: emu.get(con_name, name)

    # --- unknown / ignored constructs
    for w in emu.warnings:
        rep.add("warning", "UNPARSED", w)

    # --- timer enable
    if fld("TON") != 1:
        rep.add("error", "TON", f"{con_name}.TON is not set at the end of init: timer never runs.", 1, fld("TON"))
    # --- clock source / gate
    if fld("TCS") != 0:
        rep.add("error", "TCS", f"{con_name}.TCS=1 selects the EXTERNAL clock pin; internal Fcy needs TCS=0.", 0, fld("TCS"))
    if fld("TGATE") != 0:
        rep.add("error", "TGATE", f"{con_name}.TGATE=1 enables gated accumulation; the timer would only count while the gate input is high.", 0, fld("TGATE"))
    # --- 32-bit mode
    if n == 2:
        t32 = fld("T32")
        if spec.bits32 and t32 != 1:
            rep.add("error", "T32", "32-bit timer requested but T2CON.T32 (bit 3) is not set.", 1, t32)
        if not spec.bits32 and t32 == 1:
            rep.add("error", "T32", "T2CON.T32 is set: Timer2 runs as 32-bit Timer2/3 which was not requested (period would use PR3:PR2).", 0, t32)
    if n == 3 and emu.get("T2CON", "T32") == 1:
        rep.add("error", "T32", "T2CON.T32=1: T3CON is ignored in 32-bit mode; configure Timer2/3 through T2CON/PR2/PR3.")

    # --- period computed from the code's registers
    ps = PRESCALERS[fld("TCKPS")]
    pr = regs.get(pr_name, 0xFFFF)
    if spec.bits32:
        pr = (regs.get("PR3", 0xFFFF) << 16) | pr
    ach = timer_period_s(spec.fcy, ps, pr)
    rep.achieved_period_s = ach
    err = (ach - spec.period_s) / spec.period_s
    if abs(err) > spec.tolerance:
        rep.add("error", "PERIOD",
                f"{con_name} prescale 1:{ps} with {pr_name}={pr} gives {ach*1e3:.6g} ms at Fcy={spec.fcy/1e6:g} MHz "
                f"({err*100:+.2f}% vs requested {spec.period_s*1e3:.6g} ms). "
                f"Formula: period=(PR+1)*prescale/Fcy. A correct choice: TCKPS={ref.tckps} (1:{ref.prescale}), PR={ref.period_reg}.",
                f"{spec.period_s*1e3:.6g} ms", f"{ach*1e3:.6g} ms")
    elif abs(err) > 1e-4:
        rep.add("info", "PERIOD_ERR", f"period error {err*1e6:+.0f} ppm (within tolerance)")
    if pr_name not in wrote:
        rep.add("error" if abs(err) > spec.tolerance else "warning", "PR_UNSET",
                f"{pr_name} is never written; it resets to 0xFFFF.")
    if ps != ref.prescale and abs(err) <= spec.tolerance:
        rep.add("info", "PRESCALE", f"prescaler 1:{ps} used; reference solution uses 1:{ref.prescale}.")
    # --- ordering: PR must be loaded before TON=1 else first period is 65536 ticks
    ton_idx = next((i for i, e in enumerate(emu.events) if e.target.startswith(con_name) and
                    (e.after >> 15) & 1 and not (i and (emu.events[i - 1].after >> 15) & 1
                                                 and emu.events[i - 1].target.startswith(con_name))), None)
    pr_last = max((i for i, e in enumerate(emu.events) if e.target == pr_name), default=None)
    if ton_idx is not None and pr_last is not None and pr_last > ton_idx:
        rep.add("warning", "ORDER", f"{pr_name} is written after {con_name}.TON=1; the first period will be wrong. Load PR before enabling the timer.")
    # --- full-register writes into unimplemented bits
    for e in emu.events:
        if e.target == con_name and e.after & ~con.implemented_mask & 0xFFFF:
            rep.add("warning", "UNIMPL", f"write '{e.text}' sets unimplemented bits of {con_name} (mask 0x{con.implemented_mask:04X}).")
            break

    # --- interrupts
    ifs_r, ifs_f = ints["ifs"]
    iec_r, iec_f = ints["iec"]
    ipc_r, ipc_f = ints["ipc"]
    isr_name = ints["isr"]
    isr = next((f for f in emu.functions if f.name == isr_name), None)
    ie = emu.get(iec_r, iec_f)
    ip = emu.get(ipc_r, ipc_f)
    if spec.interrupt:
        if ie != 1:
            rep.add("error", "IE", f"{iec_r}.{iec_f} is not set: Timer interrupt never fires.", 1, ie)
        if ip == 0:
            rep.add("error", "IP", f"{ipc_r}.{ipc_f}=0 means the interrupt source is DISABLED (priority 1-7 needed).", "1..7", ip)
        elif spec.priority and ip != spec.priority:
            rep.add("error", "IP", f"{ipc_f} priority differs from request.", spec.priority, ip)
        if isr is None:
            rep.add("error", "ISR", f"no ISR named {isr_name} found (XC16 vector name for this timer).")
        else:
            if not isr.is_isr:
                rep.add("error", "ISR_ATTR", f"{isr_name} lacks the interrupt attribute (e.g. __attribute__((interrupt, auto_psv))).")
            if not isr_clears_flag(emu, isr, (ifs_r, ifs_f)):
                rep.add("error", "ISR_FLAG", f"{isr_name} does not clear {ifs_r}.{ifs_f}; it would re-enter forever.")
    else:
        if ie == 1 and isr is None:
            rep.add("error", "IE_NO_ISR", f"{iec_r}.{iec_f} enabled but no {isr_name} handler exists (would trap).")
        elif ie == 1:
            rep.add("warning", "IE", "interrupt enabled although polling was requested.")

    # --- clock macro sanity
    defs = collect_defines(strip_comments(code))
    for name, expect in (("FCY", spec.fcy), ("FOSC", spec.fosc_hz)):
        if name in defs:
            try:
                val = float(eval_expr(defs[name], lambda i: float(eval_expr(defs[i], lambda _: None)) if i in defs else None))
                if abs(val - expect) > 1:
                    rep.add("warning", "CLOCK_MACRO", f"#define {name} = {val:g} Hz but the request implies {expect:g} Hz.", f"{expect:g}", f"{val:g}")
            except EvalError:
                pass
    # --- configuration bits + clock tree vs. the Fosc used in the timing math
    if require_config:
        prof = profile or boardmod.get_profile("frcpll")
        rep.registers["CLKDIV"] = regs.get("CLKDIV", 0x0100)
        boardmod.check_config(code, spec.fosc_hz, emu.get("CLKDIV", "RCDIV"), emu.get("CLKDIV", "CPDIV"),
                              prof, ics, rep.add)
    if emu.unknown_targets - {"LATB", "LATA", "LATD", "LATE", "LATC", "LATF", "LATG", "TRISB", "TRISA", "TRISD", "TRISE", "TRISC", "TRISF", "TRISG"}:
        extra = sorted(emu.unknown_targets)[:6]
        rep.add("info", "UNMODELLED", "writes to registers the checker does not model (not verified): " + ", ".join(extra))
    return rep
