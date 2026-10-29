"""Output Compare PWM (DS39897C sec. 15, OCxCON1 p.178, OCxCON2 p.179; PPS output codes Table 10-3 p.138).

Edge-aligned PWM = OCM 110.  Self-synchronised (SYNCSEL=11111) on the system clock (OCTSEL=111):
    period = (OCxRS + 1) / Fcy,  duty = OCxR / (OCxRS + 1).
If the period does not fit 16 bits, the module syncs to a timer (SYNCSEL = 01011..01111 for T1..T5) and is
clocked by it (OCTSEL 100=T1, 000=T2, 001=T3, 010=T4, 011=T5): period = (PRx + 1) * prescale / Fcy.
OCxCON2 resets to 0x000C per the datasheet (SYNCSEL = Timer2), so code must write it explicitly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .irq import vector

OCTSEL_TIMER = {2: 0, 3: 1, 4: 2, 5: 3, 1: 4}
OCTSEL_FCY = 7
SYNC_SELF = 0x1F
SYNC_TIMER = {1: 0x0B, 2: 0x0C, 3: 0x0D, 4: 0x0E, 5: 0x0F}
PPS_CODE = {**{n: 17 + n for n in range(1, 9)}, 9: 35}       # OC1..OC8 = 18..25, OC9 = 35
OCM_EDGE, OCM_CENTER = 0b110, 0b111
_WORDS = re.compile(r"\b(pwm|output compare|OC[1-9]|duty|servo)\b", re.I)


@dataclass
class PwmSpec:
    oc: int = 1
    freq_hz: float | None = None
    duty: float = 0.5
    fcy: float = 16e6
    pin_rp: int | None = None
    timer: int | None = None          # explicit timer clock / sync source (None = self-sync on Fcy if it fits)
    tolerance: float = 0.01
    notes: list[str] = field(default_factory=list)

    def to_dict(self):
        return {k: getattr(self, k) for k in ("oc", "freq_hz", "duty", "fcy", "pin_rp", "timer")}

    @property
    def mode_timer(self) -> int | None:
        if self.timer:
            return self.timer
        if self.freq_hz and self.fcy / self.freq_hz - 1 > 0xFFFF:
            return 2
        return None


def parse_request(text: str, fcy: float = 16e6, force: bool = False) -> PwmSpec | None:
    if not force and not _WORDS.search(text):
        return None
    s = PwmSpec(fcy=fcy)
    m = re.search(r"\bOC\s*([1-9])\b|output compare\s*([1-9])", text, re.I)
    if m:
        s.oc = int(m.group(1) or m.group(2))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(k)?hz\b", text, re.I):   # MHz is the CPU clock, not PWM
        s.freq_hz = float(m.group(1)) * (1e3 if m.group(2) else 1)
        break
    m = re.search(r"period\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*(us|µs|ms|s)\b", text, re.I)
    if m and s.freq_hz is None:
        s.freq_hz = 1 / (float(m.group(1)) * {"us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1}[m.group(2).lower()])
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if m:
        s.duty = float(m.group(1)) / 100
    m = re.search(r"\b(?:timer\s*|tmr|T)([1-5])\b", text, re.I)
    if m:
        s.timer = int(m.group(1))
    from .uart import _PIN, _pin_to_rp
    m = re.search(rf"\b(?:on|pin|to|at|->|output)\s*(?:pin\s*)?{_PIN}\b", text, re.I)
    if m:
        s.pin_rp = _pin_to_rp(m.group(1))
        if s.pin_rp is None:
            s.notes.append(f"{m.group(1)} is not a remappable (RPn) pin; OC outputs need an RP pin")
    if s.freq_hz is None:
        s.notes.append("no PWM frequency stated: assuming 1 kHz")
        s.freq_hz = 1e3
    return s


def solve(spec: PwmSpec) -> dict:
    t = spec.mode_timer
    if t is None:
        rs = round(spec.fcy / spec.freq_hz) - 1
        if rs < 1:
            raise ValueError(f"{spec.freq_hz:g} Hz is above Fcy/2")
        r = round(spec.duty * (rs + 1))
        return {"mode": "self", "OCTSEL": OCTSEL_FCY, "SYNCSEL": SYNC_SELF, "OCxRS": rs, "OCxR": r,
                "freq": spec.fcy / (rs + 1), "duty": r / (rs + 1), "ticks": rs + 1, "prescale": 1}
    from .timers import solve as tsolve
    ts = timer_spec(spec)
    sol = tsolve(ts)
    ticks = sol.period_reg + 1
    r = round(spec.duty * ticks)
    return {"mode": "timer", "timer": t, "OCTSEL": OCTSEL_TIMER[t], "SYNCSEL": SYNC_TIMER[t], "OCxRS": None, "OCxR": r,
            "freq": 1 / sol.achieved_s, "duty": r / ticks, "ticks": ticks, "prescale": sol.prescale,
            "timer_regs": sol.registers}


def timer_spec(spec: PwmSpec):
    t = spec.mode_timer
    if t is None:
        return None
    from .timers import TimerSpec
    return TimerSpec(timer=t, period_s=1 / spec.freq_hz, fosc_hz=spec.fcy * 2, interrupt=False,
                     notes=[f"Timer{t} clocks OC{spec.oc} and sets the PWM period; no Timer{t} interrupt needed"])


def claims_timer(spec: PwmSpec) -> bool:
    return spec.mode_timer is not None


def _r(spec, n):
    return f"OC{spec.oc}{n}"


def calc_lines(spec: PwmSpec):
    s = solve(spec)
    oc = spec.oc
    con1 = (s["OCTSEL"] << 10) | OCM_EDGE
    con2 = s["SYNCSEL"]
    lines = [f"OC{oc} edge-aligned PWM: target {spec.freq_hz:g} Hz, {spec.duty * 100:g} % duty, Fcy={spec.fcy / 1e6:g} MHz"]
    if s["mode"] == "self":
        lines += [f"  self-sync on Fcy: OC{oc}RS = {s['OCxRS']}  OC{oc}R = {s['OCxR']}",
                  f"  achieved {s['freq']:.6g} Hz, {s['duty'] * 100:.4g} % duty (resolution 1/{s['ticks']})"]
    else:
        t = s["timer"]
        lines += [f"  Timer{t} sync/clock (1:{s['prescale']}, PR{t} = {s['ticks'] - 1}): OC{oc}R = {s['OCxR']}",
                  f"  achieved {s['freq']:.6g} Hz, {s['duty'] * 100:.4g} % duty; Timer{t} must run (TON=1)"]
    lines += [f"  OC{oc}CON1 = 0x{con1:04X}  (OCTSEL={s['OCTSEL']:03b}, OCM=110)",
              f"  OC{oc}CON2 = 0x{con2:04X}  (SYNCSEL={s['SYNCSEL']:05b}, OCTRIS=0) - write it: resets to 0x000C (Timer2 sync)"]
    if spec.pin_rp is not None:
        from .uart import rpor_field, port_pin
        reg, f = rpor_field(spec.pin_rp)
        pin = port_pin(spec.pin_rp)
        lines.append(f"  PPS: {reg}bits.{f} = {PPS_CODE[oc]} (OC{oc}) after unlocking IOLOCK; TRIS{pin[1]}{pin[2:]} = 0"
                     if pin else f"  PPS: {reg}bits.{f} = {PPS_CODE[oc]} (OC{oc})")
    else:
        lines.append(f"  PPS: map OC{oc} (output function {PPS_CODE[oc]}) to an RPn pin via RPORx")
    lines += [f"  note: {n}" for n in spec.notes]
    return lines, dict(s, OCxCON1=con1, OCxCON2=con2, spec=spec.to_dict())


def check(code: str, spec: PwmSpec, rep, emu) -> None:
    from .uart import rpor_field, port_pin, OUTPUT_RP_MAX
    try:
        s = solve(spec)
    except ValueError as e:
        rep.add("error", "PWM_SPEC", str(e))
        return
    oc = spec.oc
    c1, c2 = f"OC{oc}CON1", f"OC{oc}CON2"
    g = emu.get
    ocm = g(c1, "OCM")
    if ocm not in (OCM_EDGE, OCM_CENTER):
        rep.add("error", "PWM_OCM", f"{c1}bits.OCM must be 110 (edge-aligned PWM); got {ocm:03b} (p.178).", "110", f"{ocm:03b}")
    con2_written = any(e.target.split(".")[0] == c2 for e in emu.events)
    if not con2_written:
        rep.add("warning", "PWM_CON2", f"{c2} is never written: it resets to 0x000C (SYNCSEL=Timer2, datasheet p.179), "
                "so the PWM syncs to Timer2. Write it explicitly.")
    octsel = g(c1, "OCTSEL")
    sync = g(c2, "SYNCSEL") if con2_written else SYNC_TIMER[2]      # datasheet POR value 0x000C
    if g(c2, "OCTRIS"):
        rep.add("error", "PWM_OCTRIS", f"{c2}bits.OCTRIS=1 tri-states the OC{oc} output.", 0, 1)
    fcy = spec.fcy
    if octsel == OCTSEL_FCY:
        clk, pre, tname = fcy, 1, None
    else:
        inv = {v: k for k, v in OCTSEL_TIMER.items()}
        tn = inv.get(octsel)
        if tn is None:
            rep.add("error", "PWM_OCTSEL", f"OCTSEL={octsel:03b} is reserved.", "111 or timer", octsel)
            return
        tcon = f"T{tn}CON"
        pre = (1, 8, 64, 256)[g(tcon, "TCKPS")]
        clk, tname = fcy / pre, tn
        if g(tcon, "TON") != 1:
            rep.add("error", "PWM_TIMER", f"OC{oc} is clocked by Timer{tn} but T{tn}CONbits.TON is 0.", 1, 0)
    if sync == SYNC_SELF:
        ticks = g(f"OC{oc}RS") + 1
    elif sync in SYNC_TIMER.values():
        st = {v: k for k, v in SYNC_TIMER.items()}[sync]
        ticks = g(f"PR{st}") + 1
        if tname is not None and st != tname:
            rep.add("warning", "PWM_SYNC", f"OC{oc} is clocked by Timer{tname} but synchronised to Timer{st}.")
        if g(f"T{st}CON", "TON") != 1:
            rep.add("error", "PWM_TIMER", f"OC{oc} syncs to Timer{st} but T{st}CONbits.TON is 0 (the period never restarts).", 1, 0)
    else:
        rep.add("error", "PWM_SYNCSEL", f"{c2}bits.SYNCSEL={sync:05b}: use 11111 (self, period = OC{oc}RS) or a timer "
                "(01011..01111).", "11111", f"{sync:05b}")
        return
    freq = clk / ticks
    if abs(freq - spec.freq_hz) / spec.freq_hz > spec.tolerance:
        exp = (f"OC{oc}RS={s['OCxRS']}" if s["mode"] == "self" else f"PR{s['timer']}={s['ticks'] - 1}")
        rep.add("error", "PWM_FREQ", f"PWM frequency is {freq:.6g} Hz, request {spec.freq_hz:g} Hz.", exp,
                f"{freq:.6g} Hz")
    duty = g(f"OC{oc}R") / ticks
    if abs(duty - spec.duty) > 0.01:
        rep.add("error", "PWM_DUTY", f"duty = OC{oc}R/(period ticks) = {duty * 100:.4g} %, request {spec.duty * 100:g} %.",
                round(spec.duty * ticks), g(f"OC{oc}R"))
    # PPS output
    code_ = PPS_CODE[oc]
    mapped = [rp for rp in range(OUTPUT_RP_MAX + 1) if g(*rpor_field(rp)) == code_]
    if spec.pin_rp is not None:
        if spec.pin_rp > OUTPUT_RP_MAX:
            rep.add("error", "PWM_PPS", f"RPI{spec.pin_rp} is input-only; OC{oc} needs an RP0..RP31 output pin.")
        elif spec.pin_rp not in mapped:
            reg, f = rpor_field(spec.pin_rp)
            rep.add("error", "PWM_PPS", f"OC{oc} must be mapped: {reg}bits.{f} = {code_} (p.138).", code_, g(reg, f))
    elif not mapped:
        rep.add("error", "PWM_PPS", f"OC{oc} (output function {code_}) is not mapped to any RPn pin, so nothing is driven.")
    for rp in ([spec.pin_rp] if spec.pin_rp is not None and spec.pin_rp <= OUTPUT_RP_MAX else mapped[:1]):
        pin = port_pin(rp)
        if pin and g(f"TRIS{pin[1]}", f"TRIS{pin[1]}{pin[2:]}") != 0:
            rep.add("warning", "PWM_TRIS", f"{pin} (RP{rp}) TRIS is 1; PPS outputs drive regardless on this family, "
                    f"but clear TRIS{pin[1]}{pin[2:]} for clarity (p.136).")
    locked_write = [e for e in emu.events if e.target.startswith("RPOR")]
    if locked_write and not re.search(r"__builtin_write_OSCCONL|OSCCONbits\.IOLOCK", code):
        rep.add("warning", "PPS_UNLOCK", "RPOR is written without the IOLOCK unlock sequence (works only because IOLOCK "
                "resets to 0).")
    v = vector(f"OC{oc}Interrupt")
    if g(*v.iec):
        from .irq import check_vector
        check_vector(emu, rep, v)


def prompt_text(spec: PwmSpec) -> str:
    try:
        lines, _ = calc_lines(spec)
    except ValueError as e:
        lines = [f"!! {e}"]
    return ("Output Compare PWM facts (DS39897C sec. 15; verified later): OCM=110 edge-aligned; SYNCSEL=11111 makes "
            "OCxRS the period register; OCTSEL=111 clocks it at Fcy; OCxR = duty counts; OCxCON2 resets to 0x000C "
            "(sync to Timer2) so always write it; map OCx to a pin with RPORx (OC1..OC8 = 18..25, OC9 = 35).\n"
            + "\n".join(lines))


def pages(spec: PwmSpec) -> list[int]:
    out = [176, 178, 179, 138]
    if spec.pin_rp is not None and spec.pin_rp <= 31:
        from . import chipdb
        from .uart import rpor_field
        out.append(chipdb.REGISTERS[rpor_field(spec.pin_rp)[0]].page)
    return list(dict.fromkeys(out))


FINDING_PAGES = {"PWM_OCM": [178], "PWM_OCTSEL": [178], "PWM_CON2": [179], "PWM_SYNC": [179], "PWM_OCTRIS": [179],
                 "PWM_FREQ": [176, 179], "PWM_DUTY": [176], "PWM_PPS": [138], "PWM_TRIS": [136], "PWM_TIMER": [],
                 "PWM_SPEC": [176]}
