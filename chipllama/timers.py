"""Deterministic PIC24FJ256GB110 Timer1/2/3 calculator (the "ground truth" side).

Facts used (DS39897C): Timer period register match resets the timer, so the
period is (PRx + 1) * prescale * Tcy (the datasheet uses the same (PRy)+1 form
for the PWM period in the Output Compare chapter, p. ~192). With TCS=0 the clock
is FOSC/2 (=FCY). TCKPS<1:0>: 00=1:1, 01=1:8, 10=1:64, 11=1:256.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

PRESCALERS = {0: 1, 1: 8, 2: 64, 3: 256}
PRESCALE_TO_CODE = {v: k for k, v in PRESCALERS.items()}

# Per-timer facts: control reg, period reg, IRQ flag/enable names and priority field
TIMERS = {
    1: dict(con="T1CON", pr="PR1", ifs=("IFS0", "T1IF"), iec=("IEC0", "T1IE"), ipc=("IPC0", "T1IP"), isr="_T1Interrupt"),
    2: dict(con="T2CON", pr="PR2", ifs=("IFS0", "T2IF"), iec=("IEC0", "T2IE"), ipc=("IPC1", "T2IP"), isr="_T2Interrupt"),
    3: dict(con="T3CON", pr="PR3", ifs=("IFS0", "T3IF"), iec=("IEC0", "T3IE"), ipc=("IPC2", "T3IP"), isr="_T3Interrupt"),
}


@dataclass
class TimerSpec:
    timer: int = 1
    period_s: float | None = None        # desired interrupt/rollover period
    fosc_hz: float = 32_000_000.0        # PIC24FJ256GB110: up to 32 MHz Fosc = 16 MIPS (datasheet p.1)
    interrupt: bool = True
    priority: int | None = None          # 1..7 or None (= don't care, must be 1..7 if interrupt)
    bits32: bool = False                 # Timer2/3 32-bit mode
    tolerance: float = 0.01              # 1 % period error accepted by the checker
    notes: list[str] = field(default_factory=list)

    @property
    def fcy(self) -> float:
        return self.fosc_hz / 2.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TimerSolution:
    spec: TimerSpec
    tckps: int
    prescale: int
    period_reg: int          # PRx (or PR3:PR2 combined when bits32)
    achieved_s: float
    error_ppm: float
    con_value: int           # expected xCON value
    registers: dict[str, int]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["spec"] = self.spec.to_dict()
        return d


def timer_period_s(fcy: float, prescale: int, pr: int) -> float:
    return (pr + 1) * prescale / fcy


def solve(spec: TimerSpec) -> TimerSolution:
    if spec.timer not in TIMERS:
        raise ValueError("timer must be 1, 2 or 3")
    if spec.period_s is None or spec.period_s <= 0:
        raise ValueError("a positive period is required")
    if spec.bits32 and spec.timer == 1:
        raise ValueError("Timer1 has no 32-bit mode; use Timer2/3 (T2CON<T32>)")
    prmax = 0xFFFFFFFF if spec.bits32 else 0xFFFF
    ticks_wanted = spec.period_s * spec.fcy
    best = None
    for code in (0, 1, 2, 3):   # smallest prescaler first = best resolution
        ps = PRESCALERS[code]
        pr = round(ticks_wanted / ps) - 1
        if pr < 0:
            pr = 0
        if pr > prmax:
            continue
        ach = timer_period_s(spec.fcy, ps, pr)
        err = (ach - spec.period_s) / spec.period_s
        best = (code, ps, pr, ach, err)
        break
    if best is None:
        raise ValueError(
            f"{spec.period_s*1e3:g} ms does not fit Timer{spec.timer} at Fcy={spec.fcy/1e6:g} MHz "
            f"({'32' if spec.bits32 else '16'}-bit, max prescaler 1:256)"
            + ("" if spec.bits32 or spec.timer == 1 else "; try 32-bit mode (Timer2/3)"))
    code, ps, pr, ach, err = best
    t = TIMERS[2 if spec.bits32 else spec.timer]
    con = (1 << 15) | (code << 4)
    regs = {}
    if spec.bits32:
        con |= 1 << 3  # T32
        regs["T2CON"] = con
        regs["PR2"] = pr & 0xFFFF           # LSW
        regs["PR3"] = (pr >> 16) & 0xFFFF   # MSW
    else:
        regs[t["con"]] = con
        regs[t["pr"]] = pr
    sol = TimerSolution(spec, code, ps, pr, ach, err * 1e6, con, regs)
    return sol


def interrupt_names(spec: TimerSpec) -> dict:
    """Interrupt flag/enable/priority names. 32-bit mode uses the Timer3 interrupt (datasheet p.163-164)."""
    return TIMERS[3 if spec.bits32 else spec.timer]


# ---------- natural language parsing (deterministic; used to cross-check the model) ----------
_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT_S = {"us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "sec": 1.0, "seconds": 1.0, "second": 1.0,
           "msec": 1e-3, "usec": 1e-6, "milliseconds": 1e-3, "millisecond": 1e-3, "microseconds": 1e-6}
_UNIT_HZ = {"hz": 1.0, "khz": 1e3, "mhz": 1e6}


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def parse_request(text: str) -> TimerSpec:
    """Best-effort extraction of timer/period/clock from a plain-English request."""
    t = text.lower()
    spec = TimerSpec()
    m = re.search(r"timer\s*([123])\b", t)
    if m:
        spec.timer = int(m.group(1))
    if re.search(r"32[- ]?bit|timer\s*2\s*/\s*3|timer\s*2\s*\+\s*3|timer\s*2\s*and\s*3|t32", t):
        spec.bits32 = True
        if not m:
            spec.timer = 2
    # explicit clock: "Fosc 32 MHz", "at 8MHz", "Fcy = 4 MHz", "16 MIPS"
    m_fcy = re.search(r"f\s*cy\s*(?:=|of|:)?\s*" + _NUM + r"\s*(khz|mhz|hz)", t)
    m_mips = re.search(_NUM + r"\s*mips", t)
    m_fosc = re.search(r"(?:f\s*osc|sysclk|system clock|clock|crystal|oscillator|cpu)\s*(?:=|of|at|:)?\s*" + _NUM + r"\s*(khz|mhz|hz)", t) \
        or re.search(r"\bat\s*" + _NUM + r"\s*(khz|mhz)\b", t)
    rest = t
    for mx in (m_fcy, m_mips, m_fosc):  # remove clock phrases so they are not mistaken for the period
        if mx:
            rest = rest.replace(mx.group(0), " ")
    if m_fcy:
        spec.fosc_hz = _f(m_fcy.group(1)) * _UNIT_HZ[m_fcy.group(2)] * 2
        spec.notes.append("clock given as Fcy -> Fosc = 2*Fcy")
    elif m_mips:
        spec.fosc_hz = _f(m_mips.group(1)) * 1e6 * 2
        spec.notes.append("clock given as MIPS (=Fcy in MHz) -> Fosc = 2*Fcy")
    elif m_fosc:
        spec.fosc_hz = _f(m_fosc.group(1)) * _UNIT_HZ[m_fosc.group(2)]
    elif re.search(_NUM + r"\s*mhz", t):
        spec.fosc_hz = _f(re.search(_NUM + r"\s*mhz", t).group(1)) * 1e6
    else:
        spec.notes.append("no clock stated: assuming Fosc = 32 MHz (Fcy = 16 MHz, max for this device)")
    mp = re.search(r"(?:every|period(?:\s+of)?|each|interval(?:\s+of)?|of)\s*" + _NUM + r"\s*(us|µs|ms|msec|usec|sec|s)\b", rest) \
        or re.search(_NUM + r"\s*(us|µs|ms|msec|usec|sec|s)\b", rest)
    mf = re.search(_NUM + r"\s*(khz|hz)\b", rest)
    if mp:
        spec.period_s = _f(mp.group(1)) * _UNIT_S[mp.group(2)]
    elif mf:
        freq = _f(mf.group(1)) * _UNIT_HZ[mf.group(2)]
        if freq > 0:
            spec.period_s = 1.0 / freq
    if re.search(r"no interrupt|without interrupt|polling|poll\b", t):
        spec.interrupt = False
    elif re.search(r"interrupt|isr|toggle|blink|tick|every", t):
        spec.interrupt = True
    mpr = re.search(r"priority\s*(?:level\s*)?([1-7])", t)
    if mpr:
        spec.priority = int(mpr.group(1))
    return spec
