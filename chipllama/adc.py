"""10-bit A/D converter (DS39897C sec. 21, AD1CON1-3 p.269-271, AD1CHS p.272, AD1PCFGL p.273, AD1CSSL p.274).

TAD = TCY * (ADCS + 1) must be >= 75 ns (electrical spec AD50); a conversion takes 12 TAD; with ASAM=1 and SSRC=111
one sample+convert takes (SAMC + 12) * TAD.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .irq import vector, check_vector, prompt_line

TAD_MIN = 75e-9
CONV_TAD = 12
PAGES = [269, 270, 271, 272, 273, 274]
SSRC = {"manual": 0b111, "auto": 0b111, "timer3": 0b010, "int0": 0b001}
FORMS = {"integer": 0, "signed": 1, "fractional": 2, "signed fractional": 3}
_WORDS = re.compile(r"\b(adc|a/d|analog[- ]to[- ]digital|analog input|potentiometer|pot|sample|samples|sampling|"
                    r"AN\d{1,2}|convert|conversion)\b", re.I)


@dataclass
class AdcSpec:
    channels: list[int] = field(default_factory=lambda: [0])
    fcy: float = 16e6
    trigger: str = "manual"            # manual | auto | timer3 | int0
    rate_hz: float | None = None
    scan: bool = False
    form: str | None = None
    interrupt: bool = False
    priority: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self):
        return {k: getattr(self, k) for k in ("channels", "fcy", "trigger", "rate_hz", "scan", "form", "interrupt", "priority")}


@dataclass
class AdcSolution:
    adcs: int
    samc: int
    tad: float
    rate_hz: float | None
    smpi: int
    registers: dict

    @property
    def conv_s(self):
        return CONV_TAD * self.tad


def parse_request(text: str, fcy: float = 16e6, force: bool = False) -> AdcSpec | None:
    if not force and not _WORDS.search(text):
        return None
    if not force and not re.search(r"\b(adc|a/d|analog|AN\d|potentiometer|pot)\b", text, re.I):
        return None
    s = AdcSpec(fcy=fcy)
    chans = [int(c) for c in re.findall(r"\bAN(\d{1,2})\b", text, re.I)]
    chans += [int(c) for c in re.findall(r"\bRB(\d{1,2})\b", text, re.I)]
    s.channels = sorted(dict.fromkeys(c for c in chans if c <= 15)) or [0]
    bad = [c for c in dict.fromkeys(int(c) for c in re.findall(r"\bAN(\d{1,2})\b", text, re.I)) if c > 15]
    if bad:
        s.notes.append(f"AN{bad[0]} does not exist: the PIC24FJ256GB110 has AN0..AN15 (RB0..RB15)")
    s.scan = len(s.channels) > 1 or bool(re.search(r"\bscan", text, re.I))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k|M)?\s*(?:hz|sps|samples?\s*(?:/|per)\s*s(?:ec(?:ond)?)?)", text, re.I)
    if m:
        s.rate_hz = float(m.group(1)) * {"k": 1e3, "K": 1e3, "M": 1e6, None: 1}[m.group(2)]
    else:
        m = re.search(r"every\s*(\d+(?:\.\d+)?)\s*(us|µs|ms|s)\b", text, re.I)
        if m:
            s.rate_hz = 1 / (float(m.group(1)) * {"us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1}[m.group(2).lower()])
    if re.search(r"\b(timer\s*3|tmr3|t3)\b", text, re.I):
        s.trigger = "timer3"
    elif re.search(r"\bINT0\b", text, re.I):
        s.trigger = "int0"
    elif re.search(r"\b(auto|automatic|continuous|continuously|free[- ]running)\b", text, re.I) or s.rate_hz:
        s.trigger = "auto"
    if re.search(r"signed fractional", text, re.I):
        s.form = "signed fractional"
    elif re.search(r"fractional", text, re.I):
        s.form = "fractional"
    elif re.search(r"signed", text, re.I):
        s.form = "signed"
    s.interrupt = bool(re.search(r"\binterrupt|\bISR\b", text, re.I))
    pr = re.search(r"priority\s*(\d)", text, re.I)
    s.priority = int(pr.group(1)) if pr and s.interrupt else None
    return s


def min_adcs(fcy: float) -> int:
    return max(0, math.ceil(TAD_MIN * fcy - 1e-9) - 1)


def solve(spec: AdcSpec) -> AdcSolution:
    tcy = 1 / spec.fcy
    a0 = min_adcs(spec.fcy)
    adcs, samc = a0, 15
    if spec.trigger == "auto" and spec.rate_hz:
        best = None
        for a in range(a0, 256):
            tad = (a + 1) * tcy
            n = round(1 / (spec.rate_hz * tad))
            sm = n - CONV_TAD
            if 1 <= sm <= 31:
                err = abs(1 / ((sm + CONV_TAD) * tad) - spec.rate_hz) / spec.rate_hz
                if best is None or err < best[0] - 1e-12:
                    best = (err, a, sm)
                if err < 1e-9:
                    break
        if best is None:
            raise ValueError(f"{spec.rate_hz:g} Hz is outside the auto-sample range "
                             f"({1 / (43 * 256 * tcy):.4g} Hz .. {1 / (13 * (a0 + 1) * tcy):.4g} Hz); use the Timer3 trigger")
        _, adcs, samc = best
    tad = (adcs + 1) * tcy
    rate = 1 / ((samc + CONV_TAD) * tad) if spec.trigger == "auto" else spec.rate_hz
    if spec.trigger == "timer3" and spec.rate_hz and 1 / spec.rate_hz < (CONV_TAD + 1) * tad:
        raise ValueError(f"Timer3 trigger period {1 / spec.rate_hz * 1e6:g} us is shorter than one conversion")
    n = len(spec.channels)
    smpi = (n - 1) if spec.scan else 0
    form = FORMS.get(spec.form or "integer", 0)
    asam = 0 if spec.trigger == "manual" else 1
    con1 = (1 << 15) | (form << 8) | (SSRC[spec.trigger] << 5) | (asam << 2)
    con2 = ((1 << 10) if spec.scan else 0) | (smpi << 2)
    con3 = (samc << 8) | adcs
    regs = {"AD1CON1": con1, "AD1CON2": con2, "AD1CON3": con3}
    if spec.scan:
        regs["AD1CSSL"] = sum(1 << c for c in spec.channels)
    else:
        regs["AD1CHS"] = spec.channels[0]
    pcfg = 0xFFFF
    for c in spec.channels:
        pcfg &= ~(1 << c)
    regs["AD1PCFGL"] = pcfg
    return AdcSolution(adcs, samc, tad, rate, smpi, regs)


def timer_spec(spec: AdcSpec):
    if spec.trigger != "timer3" or not spec.rate_hz:
        return None
    from .timers import TimerSpec
    return TimerSpec(timer=3, period_s=1 / spec.rate_hz, fosc_hz=spec.fcy * 2, interrupt=False,
                     notes=["Timer3 period match triggers the ADC (SSRC=010); no Timer3 interrupt needed"])


def claims_timer(spec: AdcSpec) -> bool:
    return spec.trigger == "timer3"


def calc_lines(spec: AdcSpec):
    sol = solve(spec)
    lines = [f"ADC: Fcy={spec.fcy / 1e6:g} MHz  ADCS={sol.adcs} -> TAD={sol.tad * 1e9:g} ns (min 75 ns)  "
             f"conversion = 12 TAD = {sol.conv_s * 1e6:g} us",
             f"  channels: {', '.join('AN%d (RB%d)' % (c, c) for c in spec.channels)}" + ("  [scan]" if spec.scan else ""),
             f"  trigger: {spec.trigger} (SSRC={SSRC[spec.trigger]:03b}, ASAM={0 if spec.trigger == 'manual' else 1})"]
    if spec.trigger == "auto":
        lines.append(f"  SAMC={sol.samc} -> {sol.rate_hz:.6g} samples/s" + (" per channel / %d" % len(spec.channels) if spec.scan else "")
                     + (f" (target {spec.rate_hz:g}, err {abs(sol.rate_hz - spec.rate_hz) / spec.rate_hz * 100:.3f} %)" if spec.rate_hz else ""))
    elif spec.trigger == "manual":
        lines.append(f"  SAMC={sol.samc}: set SAMP=1, conversion starts automatically after {sol.samc} TAD; poll DONE")
    elif spec.trigger == "timer3":
        lines.append(f"  Timer3 period {1 / spec.rate_hz * 1e3:g} ms sets the rate (T3 runs, its interrupt stays off)"
                     if spec.rate_hz else "  Timer3 period sets the rate")
        if spec.scan and spec.rate_hz:
            lines.append(f"  one channel per trigger: each channel is sampled at {spec.rate_hz / len(spec.channels):g} Hz")
    for k, v in sol.registers.items():
        lines.append(f"  {k:8s} = 0x{v:04X}")
    lines.append(f"  TRISB bits {', '.join(str(c) for c in spec.channels)} = 1 (input); turn ADON on last")
    if spec.interrupt:
        lines.append(prompt_line(vector("ADC1Interrupt"), spec.priority) + f" Interrupt after SMPI+1 = {sol.smpi + 1} conversions.")
    lines += [f"  note: {n}" for n in spec.notes]
    return lines, {"spec": spec.to_dict(), "adcs": sol.adcs, "samc": sol.samc, "tad_ns": sol.tad * 1e9,
                   "rate_hz": sol.rate_hz, "smpi": sol.smpi, "registers": sol.registers}


def check(code: str, spec: AdcSpec, rep, emu) -> None:
    try:
        sol = solve(spec)
    except ValueError as e:
        rep.add("error", "ADC_SPEC", str(e))
        return
    g = emu.get
    tcy = 1 / spec.fcy
    if g("AD1CON1", "ADON") != 1:
        rep.add("error", "ADC_ADON", "AD1CON1bits.ADON is never set: the converter stays off.", 1, 0)
    adrc = g("AD1CON3", "ADRC")
    adcs = g("AD1CON3", "ADCS")
    tad = (adcs + 1) * tcy
    if not adrc and tad < TAD_MIN - 1e-15:
        rep.add("error", "ADC_TAD", f"TAD = (ADCS+1)*TCY = {tad * 1e9:.4g} ns < 75 ns minimum: set ADCS >= {sol.adcs}.",
                f">= {sol.adcs}", adcs)
    samc = g("AD1CON3", "SAMC")
    asam = g("AD1CON1", "ASAM")
    ssrc = g("AD1CON1", "SSRC")
    if ssrc == 0b111 and samc == 0:
        rep.add("error", "ADC_SAMC", "SAMC=0 with SSRC=111: zero sample time, the hold capacitor never charges (p.271).", ">= 1", 0)
    want = SSRC[spec.trigger]
    if spec.trigger == "manual":
        if ssrc not in (0, 0b111) or asam:
            rep.add("error", "ADC_SSRC", "manual conversion: ASAM=0 and SSRC=000 (clear SAMP) or 111 (auto-convert).",
                    "SSRC 000/111, ASAM 0", f"SSRC {ssrc:03b}, ASAM {asam}")
        elif not re.search(r"\bSAMP\b\s*=\s*1|\bSAMP\s*=\s*1|_SAMP\s*=\s*1", code):
            rep.add("warning", "ADC_SAMP", "manual mode: nothing sets AD1CON1bits.SAMP = 1, so no sample starts.")
    else:
        if ssrc != want:
            rep.add("error", "ADC_SSRC", f"{spec.trigger} trigger needs AD1CON1bits.SSRC={want:03b} (p.269).", f"{want:03b}", f"{ssrc:03b}")
        if not asam:
            rep.add("error", "ADC_ASAM", "ASAM must be 1 so sampling restarts after each conversion.", 1, 0)
    if spec.trigger == "auto" and spec.rate_hz and ssrc == 0b111 and asam:
        rate = 1 / ((samc + CONV_TAD) * (tad if not adrc else 250e-9))
        err = abs(rate - spec.rate_hz) / spec.rate_hz
        if adrc:
            rep.add("warning", "ADC_RATE", "ADRC=1 (internal RC clock): the sample rate is not crystal accurate.")
        elif err > 0.01:
            rep.add("error", "ADC_RATE", f"sample rate (SAMC+12)*TAD gives {rate:.6g} Hz, request {spec.rate_hz:g} Hz "
                    f"({err * 100:.3g} % off).", f"ADCS={sol.adcs}, SAMC={sol.samc}", f"ADCS={adcs}, SAMC={samc}")
    if spec.scan:
        if g("AD1CON2", "CSCNA") != 1:
            rep.add("error", "ADC_SCAN", "scanning several channels needs AD1CON2bits.CSCNA=1.", 1, 0)
        cssl = g("AD1CSSL")
        if cssl != sol.registers["AD1CSSL"]:
            rep.add("error", "ADC_CSSL", "AD1CSSL must select exactly the requested channels.",
                    f"0x{sol.registers['AD1CSSL']:04X}", f"0x{cssl:04X}")
        if g("AD1CON2", "SMPI") != sol.smpi:
            rep.add("error", "ADC_SMPI", f"SMPI must be {sol.smpi} (= channels - 1) so one interrupt/DONE covers the scan.",
                    sol.smpi, g("AD1CON2", "SMPI"))
    else:
        ch = g("AD1CHS", "CH0SA")
        if ch != spec.channels[0]:
            rep.add("error", "ADC_CH", f"AD1CHSbits.CH0SA must be {spec.channels[0]} (AN{spec.channels[0]}).", spec.channels[0], ch)
    for c in spec.channels:
        if g("AD1PCFGL", f"PCFG{c}") != 0:
            rep.add("error", "ADC_PCFG", f"AN{c} is configured digital (AD1PCFGLbits.PCFG{c}=1): it must be 0 (p.273).", 0, 1)
        if g("TRISB", f"TRISB{c}") != 1:
            rep.add("error", "ADC_TRIS", f"RB{c}/AN{c} must be an input (TRISBbits.TRISB{c}=1).", 1, 0)
    if spec.form:
        f = g("AD1CON1", "FORM")
        if f != FORMS[spec.form]:
            rep.add("error", "ADC_FORM", f"{spec.form} output needs FORM={FORMS[spec.form]:02b}.", FORMS[spec.form], f)
    if g("AD1CON2", "VCFG") not in (0,):
        rep.add("warning", "ADC_VREF", "VCFG != 000 uses external VREF+/VREF- pins; make sure they are wired.")
    # ADON must be turned on after the configuration (p.268: "configure ... then turn on the module")
    on_idx = next((i for i, e in enumerate(emu.events) if e.target.startswith("AD1CON1") and (e.after >> 15) & 1), None)
    if on_idx is not None:
        late = [e for e in emu.events[on_idx + 1:] if e.target.split(".")[0] in ("AD1CON2", "AD1CON3", "AD1CSSL")
                or (e.target.startswith("AD1CON1") and e.target not in ("AD1CON1.SAMP", "AD1CON1.DONE", "AD1CON1.ADON"))]
        if late:
            rep.add("warning", "ADC_ORDER", f"'{late[0].text}' changes the ADC configuration after ADON=1; configure first, "
                    "turn ADON on last.")
    if not re.search(r"\bADC1BUF[0-9A-F]\b", code):
        rep.add("warning", "ADC_READ", "the result buffer ADC1BUFn is never read.")
    if spec.interrupt:
        check_vector(emu, rep, vector("ADC1Interrupt"), priority=spec.priority, what="ADC")
        isr = next((f for f in emu.functions if f.name == "_ADC1Interrupt"), None)
        if isr and not re.search(r"\bADC1BUF[0-9A-F]\b", isr.body):
            rep.add("warning", "ADC_ISR_READ", "_ADC1Interrupt does not read ADC1BUFn.")


def prompt_text(spec: AdcSpec) -> str:
    try:
        lines, _ = calc_lines(spec)
    except ValueError as e:
        lines = [f"!! {e}"]
    return ("10-bit ADC facts (DS39897C sec. 21; verified later): AD1PCFGL bit = 0 makes ANx analog; "
            "TAD = TCY*(ADCS+1) >= 75 ns; conversion 12 TAD; SSRC 111 = internal counter (SAMC TAD sample time), "
            "010 = Timer3 period match, 000 = clear SAMP manually; scan uses CSCNA=1 + AD1CSSL and SMPI = n-1; "
            "results in ADC1BUF0..F; set ADON last.\n" + "\n".join(lines))


def pages(spec: AdcSpec) -> list[int]:
    out = list(PAGES)
    if spec.interrupt:
        out += vector("ADC1Interrupt").pages[1:]
    return list(dict.fromkeys(out))


FINDING_PAGES = {"ADC_ADON": [269], "ADC_SSRC": [269], "ADC_ASAM": [269], "ADC_FORM": [269], "ADC_SAMP": [269],
                 "ADC_TAD": [271], "ADC_SAMC": [271], "ADC_RATE": [271], "ADC_SCAN": [270], "ADC_SMPI": [270],
                 "ADC_CSSL": [274], "ADC_CH": [272], "ADC_PCFG": [273], "ADC_TRIS": [133], "ADC_VREF": [270],
                 "ADC_ORDER": [268], "ADC_READ": [], "ADC_ISR": [], "ADC_SPEC": [271]}
