"""Power saving: Sleep/Idle (DS39897C sec. 9, p.131), Doze (CLKDIV DOZE/DOZEN, p.125/132), Watchdog timer
(CW1 FWDTEN<7>, WINDIS<6>, FWPSA<4>, WDTPS<3:0>, p.288; RCON.SWDTEN; LPRC 31 kHz: period = prescale*postscale/31 kHz)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LPRC = 31000.0
_WORDS = re.compile(r"\b(sleep|idle|doze|watchdog|wdt|low[- ]power|power[- ]saving)\b", re.I)


@dataclass
class PowerSpec:
    sleep: bool = False
    idle: bool = False
    doze: int | None = None            # ratio 1..128
    wdt_s: float | None = None
    notes: list[str] = field(default_factory=list)
    fcy: float = 16e6

    def to_dict(self):
        return {"sleep": self.sleep, "idle": self.idle, "doze": self.doze, "wdt_s": self.wdt_s,
                "wdt": wdt_solve(self.wdt_s) if self.wdt_s else None}


def wdt_solve(t: float) -> dict:
    best = None
    for pre, name in ((32, "PR32"), (128, "PR128")):
        for k in range(16):
            post = 1 << k
            p = pre * post / LPRC
            cand = (abs(p - t) / t, p, name, f"PS{post}")
            best = min(best, cand) if best else cand
    return {"period_s": best[1], "FWPSA": best[2], "WDTPS": best[3]}


def parse_request(text, fcy=16e6, force=False):
    if not force and not _WORDS.search(text):
        return None
    s = PowerSpec(fcy=fcy)
    t = text.lower()
    s.sleep = bool(re.search(r"\bsleep", t))
    s.idle = bool(re.search(r"\bidle", t))
    m = re.search(r"doze\D{0,15}1\s*:\s*(\d+)|doze\D{0,15}(\d+)", t)
    if m:
        r = int(m.group(1) or m.group(2))
        s.doze = r if r in (1, 2, 4, 8, 16, 32, 64, 128) else 8
    elif "doze" in t:
        s.doze = 8
    if re.search(r"watchdog|\bwdt\b", t):
        m = re.search(r"(\d+(?:\.\d+)?)\s*(ms|s)\b", t)
        s.wdt_s = float(m.group(1)) * (1e-3 if m.group(2) == "ms" else 1) if m else 1.0
        if not m:
            s.notes.append("no watchdog timeout stated: assuming ~1 s")
    if not (s.sleep or s.idle or s.doze or s.wdt_s):
        s.sleep = True
    return s


def claims_timer(spec):
    return spec.wdt_s is not None


def allow_config(spec) -> set:
    return {"FWDTEN"} if spec.wdt_s else set()


def calc_lines(spec):
    L = ["Power management (p.131-132, p.288):"]
    if spec.sleep or spec.idle:
        L.append(f"  {'Sleep()' if spec.sleep else 'Idle()'}  (XC16 macro = PWRSAV #{0 if spec.sleep else 1}); "
                 "enable a wake source first: an interrupt with IEx=1 (IPL above CPU IPL wakes and vectors), CN, INTx, WDT, RTCC")
        if spec.sleep:
            L.append("  Sleep stops Fosc: only LPRC/SOSC-clocked sources (WDT, RTCC, CN, INTx, Timer1 on SOSC) can wake it")
    if spec.doze:
        code = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5, 64: 6, 128: 7}[spec.doze]
        L.append(f"  Doze 1:{spec.doze}: CLKDIVbits.DOZE = {code}; CLKDIVbits.DOZEN = 1  (CPU slowed, peripherals stay at Fcy)")
    if spec.wdt_s:
        w = wdt_solve(spec.wdt_s)
        L += [f"  WDT ~{w['period_s']*1e3:.4g} ms: #pragma config FWDTEN = ON, FWPSA = {w['FWPSA']}, WDTPS = {w['WDTPS']}, WINDIS = OFF",
              "  call ClrWdt() in the main loop (well inside the period; LPRC is +-15%)",
              "  alternative: FWDTEN = OFF and RCONbits.SWDTEN = 1 to enable it from software"]
    L += [f"  note: {x}" for x in spec.notes]
    return L, spec.to_dict()


def check(code, spec, rep, emu):
    from .board import parse_pragmas
    vals, _ = parse_pragmas(code)
    if (spec.sleep and not re.search(r"\bSleep\s*\(\)|PWRSAV\s*#?0|__builtin_pwrsav\s*\(\s*0", code)):
        rep.add("error", "PWR_SLEEP", "no Sleep() / __builtin_pwrsav(0) instruction found.")
    if (spec.idle and not re.search(r"\bIdle\s*\(\)|PWRSAV\s*#?1|__builtin_pwrsav\s*\(\s*1", code)):
        rep.add("error", "PWR_IDLE", "no Idle() / __builtin_pwrsav(1) instruction found.")
    if spec.sleep or spec.idle:
        iec = any(e.target.startswith("IEC") and e.after for e in emu.events)
        if not iec and vals.get("FWDTEN") != "ON" and not emu.get("RCON", "SWDTEN"):
            rep.add("error", "PWR_WAKE", "no wake source is enabled (no IECx bit, no WDT): the device sleeps forever.")
    if spec.doze:
        code_ = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5, 64: 6, 128: 7}[spec.doze]
        if emu.get("CLKDIV", "DOZE") != code_:
            rep.add("error", "PWR_DOZE", f"CLKDIVbits.DOZE should be {code_} for 1:{spec.doze} (p.125).", code_, emu.get("CLKDIV", "DOZE"))
        if emu.get("CLKDIV", "DOZEN") != 1:
            rep.add("error", "PWR_DOZEN", "CLKDIVbits.DOZEN is not set: Doze is off.", 1, 0)
    if spec.wdt_s:
        w = wdt_solve(spec.wdt_s)
        on = vals.get("FWDTEN") == "ON" or emu.get("RCON", "SWDTEN") == 1
        if not on:
            rep.add("error", "WDT_OFF", "watchdog requested but FWDTEN = OFF and RCONbits.SWDTEN is never set (p.288).")
        if vals.get("FWPSA") != w["FWPSA"] or vals.get("WDTPS") != w["WDTPS"]:
            pre = 32 if vals.get("FWPSA") == "PR32" else 128
            post = int(vals.get("WDTPS", "PS32768")[2:])
            act = pre * post / LPRC
            if abs(act - spec.wdt_s) / spec.wdt_s > 0.5:
                rep.add("error", "WDT_PERIOD", f"WDT period = {pre}*{post}/31 kHz = {act*1e3:.4g} ms, request "
                        f"{spec.wdt_s*1e3:g} ms: use FWPSA = {w['FWPSA']}, WDTPS = {w['WDTPS']}.", w["WDTPS"], vals.get("WDTPS"))
        if not re.search(r"ClrWdt\s*\(\)|CLRWDT|__builtin_clrwdt", code):
            rep.add("error", "WDT_CLR", "WDT enabled but ClrWdt() is never called: the device will reset every period.")


def prompt_text(spec):
    return ("Power facts (DS39897C; verified later): Sleep()/Idle() macros from <xc.h> (PWRSAV); CLKDIV DOZE<14:12> DOZEN<11> (p.125); "
            "CW1 FWDTEN/WINDIS/FWPSA/WDTPS (p.288), RCON.SWDTEN<5>, RCON.WDTO/SLEEP/IDLE flags (p.72); ClrWdt() resets the WDT.\n"
            + "\n".join(calc_lines(spec)[0]))


def pages(spec):
    return [131, 132, 288, 72]


FINDING_PAGES = {"PWR_SLEEP": [131], "PWR_IDLE": [131], "PWR_WAKE": [131], "PWR_DOZE": [125], "PWR_DOZEN": [125],
                 "WDT_OFF": [288], "WDT_PERIOD": [288], "WDT_CLR": [288]}


# ---- rough current estimate (typical, 3.3 V, 25 C).  Order-of-magnitude figures in the spirit of DS39897C
# sec. 26 (IDD/IIDLE/IPD tables); NOT verified against the exact table rows - use a meter for real numbers.
IDD_PER_MIPS_MA = 0.55      # run mode, ~ mA per MIPS
IIDLE_FRAC = 0.30           # Idle ~ 30 % of run current
IPD_UA = 20.0               # Sleep, WDT off, typical
WDT_UA = 1.0
PERIPH_MA = {"uart": 0.3, "spi": 0.3, "i2c": 0.2, "adc": 0.6, "timer": 0.05, "oc": 0.05, "usb": 6.0}


def estimate(fcy: float = 16e6, peripherals: list[str] | None = None, mode: str = "run", doze: int = 1,
             duty: float = 1.0, wdt: bool = False, battery_mah: float | None = None) -> dict:
    """mode run|idle|sleep; duty = fraction of time awake (rest asleep). Returns mA figures + battery life."""
    mips = fcy / 1e6
    per = sum(PERIPH_MA.get(p.lower(), 0.1) for p in (peripherals or []))
    run = IDD_PER_MIPS_MA * mips / (1 if doze <= 1 else 1 + 0.5 * (1 - 1 / doze)) + per
    awake = run if mode == "run" else run * IIDLE_FRAC if mode == "idle" else (IPD_UA + (WDT_UA if wdt else 0)) / 1000
    sleep = (IPD_UA + (WDT_UA if wdt else 0)) / 1000
    avg = awake * duty + sleep * (1 - duty)
    out = {"run_ma": round(run, 3), "awake_ma": round(awake, 4), "sleep_ma": round(sleep, 4), "avg_ma": round(avg, 4),
           "note": "typical-order estimate, not a datasheet guarantee (DS39897C sec. 26)"}
    if battery_mah:
        out["battery_hours"] = round(battery_mah / avg, 1) if avg > 0 else None
    return out
