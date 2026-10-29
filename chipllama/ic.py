"""Input Capture (DS39897C sec. 13 p.169; ICxCON1 p.171, ICxCON2 p.172; PPS inputs RPINR7..10/15 p.143-146).

ICM: 001 every edge, 010 every falling, 011 every rising, 100 every 4th rising, 101 every 16th rising.
ICTSEL: 000 = Timer3, 001 = Timer2, 010 = Timer4, 011 = Timer5, 100 = Timer1, 111 = Fcy.
NOTE the swap: OC's OCTSEL has 000 = Timer2, 001 = Timer3, IC's ICTSEL has 000 = Timer3.
ICI: interrupt on every (ICI+1)-th capture; the ISR must read ICxBUF (4-deep FIFO, ICOV on overflow).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .irq import check_vector, vector

ICTSEL = {3: 0, 2: 1, 4: 2, 5: 3, 1: 4}
ICM = {"every": 1, "falling": 2, "rising": 3, "4th": 4, "16th": 5}
RPINR = {1: ("RPINR7", "IC1R"), 2: ("RPINR7", "IC2R"), 3: ("RPINR8", "IC3R"), 4: ("RPINR8", "IC4R"),
         5: ("RPINR9", "IC5R"), 6: ("RPINR9", "IC6R"), 7: ("RPINR10", "IC7R"), 8: ("RPINR10", "IC8R"),
         9: ("RPINR15", "IC9R")}
PRESCALES = (1, 8, 64, 256)
_WORDS = re.compile(r"\b(input capture|capture|IC[1-9]|pulse width|measure (the )?(period|frequency))\b", re.I)


@dataclass
class IcSpec:
    ic: int = 1
    edge: str = "rising"
    pin_rp: int | None = None
    timer: int = 3
    max_period_s: float | None = None
    interrupt: bool = True
    priority: int | None = None
    every: int = 1                      # captures per interrupt (ICI+1)
    fcy: float = 16e6
    notes: list[str] = field(default_factory=list)

    def to_dict(self):
        return {k: getattr(self, k) for k in ("ic", "edge", "pin_rp", "timer", "max_period_s", "interrupt", "priority",
                                              "every", "fcy")}

    @property
    def prescale(self) -> int:
        need = self.max_period_s or 0
        for p in PRESCALES:
            if 65536 * p / self.fcy >= need:
                return p
        raise ValueError(f"{need:g} s exceeds a 16-bit Timer at 1:256 ({65536 * 256 / self.fcy:g} s)")


def parse_request(text: str, fcy: float = 16e6, force: bool = False) -> IcSpec | None:
    if not force and (not _WORDS.search(text) or
                      (re.search(r"\bgated?\b", text, re.I) and not re.search(r"capture|\bIC[1-9]\b", text, re.I))):
        return None                       # "gated mode: measure pulse width" is a Timer gate, not Input Capture
    s = IcSpec(fcy=fcy)
    m = re.search(r"\bIC\s*([1-9])\b|input capture\s*([1-9])", text, re.I)
    if m:
        s.ic = int(m.group(1) or m.group(2))
    if re.search(r"16(th)?\s*rising", text, re.I):
        s.edge = "16th"
    elif re.search(r"\b4(th)?\s*rising", text, re.I):
        s.edge = "4th"
    elif re.search(r"\b(every|both|any|each) edges?\b|pulse width", text, re.I):
        s.edge = "every"
    elif re.search(r"falling", text, re.I):
        s.edge = "falling"
    m = re.search(r"\b(?:timer\s*|tmr)([1-5])\b", text, re.I)
    if m:
        s.timer = int(m.group(1))
    m = re.search(r"(?:up to|max(?:imum)?|longest|<=?)\s*(\d+(?:\.\d+)?)\s*(us|µs|ms|s)\b", text, re.I)
    if m:
        s.max_period_s = float(m.group(1)) * {"us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1}[m.group(2).lower()]
    m = re.search(r"(?:down to|min(?:imum)?|lowest)\s*(\d+(?:\.\d+)?)\s*(k)?hz", text, re.I)
    if m and s.max_period_s is None:
        s.max_period_s = 1 / (float(m.group(1)) * (1e3 if m.group(2) else 1))
    m = re.search(r"priority\s*(\d)", text, re.I)
    if m:
        s.priority = int(m.group(1))
    if re.search(r"\b(poll|polling|no interrupt|without interrupt)\b", text, re.I):
        s.interrupt = False
    m = re.search(r"(?:interrupt|isr)\s*(?:on\s*)?every\s*([2-4])\s*captures?", text, re.I)
    if m:
        s.every = int(m.group(1))
    from .uart import _PIN, _pin_to_rp
    m = re.search(rf"\b(?:on|pin|from|at)\s*(?:pin\s*)?{_PIN}\b", text, re.I)
    if m:
        s.pin_rp = _pin_to_rp(m.group(1))
        if s.pin_rp is None:
            s.notes.append(f"{m.group(1)} is not remappable; IC inputs need an RPn/RPIn pin")
    return s


def timer_spec(spec: IcSpec):
    from .timers import TimerSpec
    p = spec.prescale
    return TimerSpec(timer=spec.timer, period_s=65536 * p / spec.fcy, fosc_hz=spec.fcy * 2, interrupt=False,
                     notes=[f"Timer{spec.timer} free-runs (PR{spec.timer}=0xFFFF, 1:{p}) as the IC{spec.ic} time base"])


def claims_timer(spec: IcSpec) -> bool:
    return True


def calc_lines(spec: IcSpec):
    p = spec.prescale
    t, n = spec.timer, spec.ic
    con1 = (ICTSEL[t] << 10) | ((spec.every - 1) << 5) | ICM[spec.edge]
    tick = p / spec.fcy
    reg, f = RPINR[n]
    lines = [f"IC{n}: capture on {spec.edge} edge{'s' if spec.edge == 'every' else ''}, time base Timer{t}",
             f"  Timer{t}: 1:{p}, PR{t} = 0xFFFF, TON=1 -> tick {tick * 1e6:.4g} us, range {65536 * tick * 1e3:.4g} ms",
             f"  IC{n}CON1 = 0x{con1:04X}  (ICTSEL={ICTSEL[t]:03b} [000=T3, 001=T2: swapped vs OCTSEL], "
             f"ICI={spec.every - 1:02b}, ICM={ICM[spec.edge]:03b})",
             f"  IC{n}CON2 = 0x0000 (no sync/trigger; resets to 0x000D)",
             f"  IC{n}TMR free-runs 0..0xFFFF; period = (ICxBUF[n] - ICxBUF[n-1]) * {tick * 1e6:.4g} us (unsigned 16-bit subtraction handles rollover)"]
    if spec.pin_rp is not None:
        from .uart import port_pin
        pin = port_pin(spec.pin_rp)
        lines.append(f"  PPS: {reg}bits.{f} = {spec.pin_rp}" + (f"; {pin} input (TRIS=1, digital)" if pin else ""))
    else:
        lines.append(f"  PPS: set {reg}bits.{f} to the RPn number of the input pin")
    v = vector(f"IC{n}Interrupt")
    if spec.interrupt:
        lines.append(f"  interrupt: {v.iec[0]}bits.{v.iec[1]}=1, {v.ipc[0]}bits.{v.ipc[1]}={spec.priority or 4}; "
                     f"_IC{n}Interrupt reads IC{n}BUF and clears {v.ifs[1]}")
    lines += [f"  note: {x}" for x in spec.notes]
    return lines, {"ICxCON1": con1, "prescale": p, "tick_s": tick, "spec": spec.to_dict()}


def check(code: str, spec: IcSpec, rep, emu) -> None:
    from .uart import RP_PIN
    n, g = spec.ic, emu.get
    c1 = f"IC{n}CON1"
    icm = g(c1, "ICM")
    if icm != ICM[spec.edge]:
        rep.add("error", "IC_ICM", f"{c1}bits.ICM must be {ICM[spec.edge]:03b} for {spec.edge} edge capture (p.171).",
                f"{ICM[spec.edge]:03b}", f"{icm:03b}")
    ts = g(c1, "ICTSEL")
    inv = {v: k for k, v in ICTSEL.items()}
    t = inv.get(ts)
    if ts == 7:
        t = None
    elif t is None:
        rep.add("error", "IC_ICTSEL", f"ICTSEL={ts:03b} is reserved.", f"{ICTSEL[spec.timer]:03b}", f"{ts:03b}")
        return
    elif t != spec.timer:
        swap = {2: 3, 3: 2}.get(spec.timer) == t
        rep.add("error", "IC_ICTSEL", f"IC{n} uses Timer{t} (ICTSEL={ts:03b}) but the request uses Timer{spec.timer}"
                + (" - ICTSEL 000=Timer3, 001=Timer2 (swapped vs OCTSEL)" if swap else "") + ".",
                f"{ICTSEL[spec.timer]:03b}", f"{ts:03b}")
    if t is not None:
        if g(f"T{t}CON", "TON") != 1:
            rep.add("error", "IC_TIMER", f"Timer{t} is IC{n}'s time base but T{t}CONbits.TON is 0: every capture reads 0.", 1, 0)
    c2 = f"IC{n}CON2"
    written = any(e.target.split(".")[0] == c2 for e in emu.events)
    sync = g(c2, "SYNCSEL") if written else 0x0D               # datasheet POR 0x000D: sync to Timer3
    if not written:
        rep.add("warning", "IC_CON2", f"{c2} is never written: it resets to 0x000D (SYNCSEL=Timer3, p.172), so IC{n}TMR "
                "is reset by Timer3's period match. Write IC{n}CON2 = 0 for free-running capture.".replace("{n}", str(n)))
    st = {0x0B: 1, 0x0C: 2, 0x0D: 3, 0x0E: 4, 0x0F: 5}.get(sync)
    if st is not None and g(f"PR{st}") != 0xFFFF:
        rep.add("warning", "IC_RANGE", f"IC{n}TMR is synchronised to Timer{st} with PR{st}=0x{g(f'PR{st}'):04X}: it wraps "
                f"early, so BUF differences are wrong across rollover. Use IC{n}CON2=0 (free-running) or PR{st}=0xFFFF.")
    if t is not None:
        pre = PRESCALES[g(f"T{t}CON", "TCKPS")]
        if spec.max_period_s and 65536 * pre / spec.fcy < spec.max_period_s:
            rep.add("error", "IC_RANGE", f"Timer{t} at 1:{pre} spans {65536 * pre / spec.fcy * 1e3:.4g} ms < requested "
                    f"{spec.max_period_s * 1e3:g} ms.", f"1:{spec.prescale}", f"1:{pre}")
    reg, f = RPINR[n]
    got = g(reg, f)
    if spec.pin_rp is not None and got != spec.pin_rp:
        rep.add("error", "IC_PPS", f"IC{n} must be mapped with {reg}bits.{f} = {spec.pin_rp} (p.143).", spec.pin_rp, got)
    elif spec.pin_rp is None and got == 0x3F:
        rep.add("error", "IC_PPS", f"{reg}bits.{f} is unmapped (0x3F): IC{n} has no input pin.")
    pin = RP_PIN.get(spec.pin_rp if spec.pin_rp is not None else got)
    if pin:
        tr, tb = f"TRIS{pin[1]}", f"TRIS{pin[1]}{pin[2:]}"
        if g(tr, tb) != 1:
            rep.add("error", "IC_TRIS", f"IC{n} pin {pin} must be an input ({tr}bits.{tb}=1).", 1, g(tr, tb))
        if pin.startswith("RB") and g("AD1PCFGL", f"PCFG{pin[2:]}") != 1:
            rep.add("error", "IC_ANALOG", f"IC{n} pin {pin} is AN{pin[2:]}: set AD1PCFGLbits.PCFG{pin[2:]}=1 (p.273).", 1, 0)
    if g(c1, "ICI") != spec.every - 1:
        rep.add("warning", "IC_ICI", f"{c1}bits.ICI={g(c1, 'ICI')}: interrupt every {g(c1, 'ICI') + 1} captures, request "
                f"{spec.every}.", spec.every - 1, g(c1, "ICI"))
    if not re.search(rf"\bIC{n}BUF\b", code):
        rep.add("error", "IC_READ", f"IC{n}BUF is never read: the 4-deep FIFO fills and ICOV stops capturing.")
    if spec.interrupt:
        v = vector(f"IC{n}Interrupt")
        check_vector(emu, rep, v, priority=spec.priority, what=f"IC{n}")
        isr = next((fn for fn in emu.functions if fn.name == v.isr), None)
        if isr and not re.search(rf"\bIC{n}BUF\b", isr.body):
            rep.add("warning", "IC_ISR_READ", f"{v.isr} does not read IC{n}BUF (FIFO overflows, ICOV=1).")


def prompt_text(spec: IcSpec) -> str:
    try:
        lines, _ = calc_lines(spec)
    except ValueError as e:
        lines = [f"!! {e}"]
    return ("Input Capture facts (DS39897C sec. 14; verified later): ICTSEL 000=Timer3, 001=Timer2 (swapped vs OC), "
            "100=Timer1; let the timer free-run with PRx=0xFFFF and TON=1; map the pin with RPINRx ICnR and make it a "
            "digital input; read ICxBUF in the ISR (FIFO) and clear ICxIF.\n" + "\n".join(lines))


def pages(spec: IcSpec) -> list[int]:
    from . import chipdb
    return list(dict.fromkeys([169, 171, 172, chipdb.REGISTERS[RPINR[spec.ic][0]].page] + vector(f"IC{spec.ic}Interrupt").pages))


FINDING_PAGES = {"IC_ICM": [171], "IC_ICTSEL": [171], "IC_ICI": [171], "IC_TIMER": [], "IC_RANGE": [], "IC_CON2": [172],
                 "IC_PPS": [143], "IC_TRIS": [133], "IC_ANALOG": [273], "IC_READ": [169], "IC_ISR_READ": [169]}
