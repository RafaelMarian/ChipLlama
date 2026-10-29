from __future__ import annotations

from . import chipdb
from . import board as boardmod
from .timers import TimerSpec, TimerSolution, TIMERS, interrupt_names

SYSTEM = """You are ChipLlama, an expert embedded firmware engineer for the Microchip PIC24FJ256GB110 family (16-bit, DS39897C), compiled with MPLAB XC16 (xc16-gcc).
You write peripheral initialisation code whose register values are VERIFIED afterwards by a deterministic checker and a compiler, so be exact.

Rules:
- Reply with ONE complete, compilable C file in a single ```c fenced block, then at most 5 lines of explanation after it.
- `#include <xc.h>`. Use XC16 register names: T1CON, T1CONbits.TCKPS, PR1, IEC0bits.T1IE, IFS0bits.T1IF, IPC0bits.T1IP ...
- Define the clock as macros: `#define FOSC <Hz>UL` and `#define FCY (FOSC/2UL)`. Fcy = Fosc/2 on this device.
- Show the bit math explicitly: compute prescaler and PR with macros/constant expressions and comment each step, e.g. `PR1 = (FCY / PRESCALE / FREQ) - 1`. Period = (PR+1) * prescale / Fcy.
- Load PRx and clear TMRx BEFORE setting TON. Clear the interrupt flag, set priority (1-7), then enable the interrupt, then start the timer.
- Interrupt handlers must use the XC16 vector name (_T1Interrupt, _T2Interrupt, _T3Interrupt) with `__attribute__((interrupt, auto_psv))` and clear the flag first.
- ALWAYS start the file (right after `#include <xc.h>`) with the complete `#pragma config` block given in the request, every line, unchanged unless the requested Fosc needs a different clock mode. The board is bare (no crystal, nothing else sets the configuration words), so code without them does not run as intended.
- The clock configuration must produce exactly the FOSC used in the timing math: FRCPLL -> PLL input must be 4 MHz (FRC 8 MHz / RCDIV / PLLDIV), PLL 96 MHz / 3 = 32 MHz, then CLKDIV.CPDIV (0=32,1=16,2=8,3=4 MHz). FRC = 8 MHz. Never use POSCMOD other than NONE or FNOSC=PRI/PRIPLL unless the request says a crystal is fitted.
- Run the given clock statements (CLKDIVbits...) first in main(), before any peripheral init. Do not use _CONFIGx() macros and do not invent registers. Include a `main()` that calls the init function and loops forever.
- If the datasheet page images contradict your memory, trust the images and the register table given below."""


def register_table_text(spec: TimerSpec) -> str:
    n = 2 if spec.bits32 else spec.timer
    names = [TIMERS[n]["con"], TIMERS[n]["pr"]]
    ints = interrupt_names(spec)
    names += [ints["ifs"][0], ints["iec"][0], ints["ipc"][0]]
    if spec.bits32:
        names += ["T3CON", "PR3"]
    lines = ["Register facts (from DS39897C; bit numbers are exact):"]
    for nm in dict.fromkeys(names):
        if nm in chipdb.REGISTERS:
            r = chipdb.REGISTERS[nm]
            relevant = [f for f in r.fields if nm.startswith("T") or f.name in (ints["ifs"][1], ints["iec"][1], ints["ipc"][1])]
            fs = ", ".join(f"{f.name}<{f.lsb + f.width - 1}:{f.lsb}>" if f.width > 1 else f"{f.name}<{f.lsb}>" for f in relevant)
            lines.append(f"- {nm} @0x{r.address:04X} (datasheet p.{r.page}): {fs}")
            if nm.startswith("T") and nm.endswith("CON"):
                lines.append("    TCKPS: 00=1:1, 01=1:8, 10=1:64, 11=1:256; TCS: 0=internal Fosc/2, 1=external pin; TGATE must be 0 for free-running; unimplemented bits read 0")
        elif nm in chipdb.DATA_REGS:
            lines.append(f"- {nm} @0x{chipdb.DATA_REGS[nm]:04X}: 16-bit, resets to 0xFFFF")
    lines.append("- IPCx priority field = 0 disables that interrupt source; reset default is priority 4.")
    if spec.bits32:
        lines.append("- 32-bit mode: T2CON<T32>=1; PR3 = most significant word, PR2 = least significant word; T3CON is ignored; interrupt is Timer3's (T3IF/T3IE/T3IP, _T3Interrupt).")
    return "\n".join(lines)


def spec_text(spec: TimerSpec) -> str:
    return (f"Timer{'2/3 (32-bit)' if spec.bits32 else spec.timer}, target period {spec.period_s*1e3:.6g} ms, "
            f"Fosc = {spec.fosc_hz/1e6:g} MHz (Fcy = {spec.fcy/1e6:g} MHz), interrupt = {'yes' if spec.interrupt else 'no (polling)'}"
            + (f", priority {spec.priority}" if spec.priority else "") + f", tolerance {spec.tolerance*100:g}%")


def board_text(profile: "boardmod.BoardProfile", ics: str) -> str:
    s = [f"Board/clock profile: {profile.title}. Programmer/debugger wired to PGEC{ics[-1]}/PGED{ics[-1]}.",
         "Emit exactly this configuration block:", "```c", boardmod.render_pragmas(profile, ics), "```"]
    if profile.clock_code:
        s += ["First statements in main():", *("    " + c for c in profile.clock_code)]
    if profile.note:
        s.append("Note: " + profile.note)
    return "\n".join(s)


def first_user_prompt(request: str, spec: TimerSpec, solution_hint: TimerSolution | None, image_note: str,
                      profile: "boardmod.BoardProfile | None" = None, ics: str = "PGx2") -> str:
    profile = profile or boardmod.get_profile("frcpll")
    s = [f"Request: {request}", "", "Parameters extracted by the tool (verified later): " + spec_text(spec), "",
         board_text(profile, ics), "", register_table_text(spec)]
    if image_note:
        s += ["", image_note]
    if solution_hint:
        s += ["", f"Calculator hint: TCKPS={solution_hint.tckps} (1:{solution_hint.prescale}), "
                  f"PR={solution_hint.period_reg}, {solution_hint.achieved_s*1e3:.6g} ms achieved."]
    s += ["", "Write the C file now."]
    return "\n".join(s)


def feedback_prompt(compile_fb: str, check_fb: str) -> str:
    parts = ["Your code failed automatic verification. Fix ALL issues and return the complete corrected file in one ```c block."]
    if compile_fb:
        parts += ["", "xc16-gcc errors:", compile_fb]
    if check_fb:
        parts += ["", "Register checker findings:", check_fb]
    return "\n".join(parts)


EXTRACT_SPEC = """Extract timer parameters from the request as JSON only, no prose:
{"timer": 1|2|3, "bits32": bool, "period_ms": number|null, "freq_hz": number|null, "fosc_mhz": number|null, "interrupt": bool, "priority": 1-7|null}
Request: """

DEBUG_SYSTEM = """You are ChipLlama's reverse-debugging assistant for the PIC24FJ256GB110 (DS39897C). You are given register values that a deterministic decoder has ALREADY decoded and a list of rule-based findings. Explain the most likely root cause of the user's symptom in plain language, referring to exact register.bit names, then give a concrete fix as C snippet using XC16 register names. Do not contradict the decoded values. Keep it under 250 words."""

VISION_TRANSCRIBE = """These images show a debugger register/SFR view or a register dump for a PIC24FJ256GB110. Transcribe EVERY special-function-register value you can read. Output only JSON mapping register name to hex string, e.g. {"T1CON":"0x8030","PR1":"0x270F"}. If a value is unreadable omit it. Do not guess."""


# ---------------------------------------------------------------- any PIC (EDC-driven)
def generic_system(dev) -> str:
    comp = {"xc8": "MPLAB XC8 (xc8-cc)", "xc16": "MPLAB XC16 (xc16-gcc)", "xc32": "MPLAB XC32 (xc32-gcc)"}[dev.compiler]
    cyc = {"xc8": "Fcy = Fosc/4 on 8-bit PICs", "xc16": "Fcy = Fosc/2 on 16-bit PICs", "xc32": "peripheral clock = PBCLK"}[dev.compiler]
    isr = {"xc8": "XC8 ISRs: `void __interrupt() isr(void)` (PIC18 may use `__interrupt(high_priority)`)",
           "xc16": "XC16 ISRs: `void __attribute__((interrupt, auto_psv)) _T1Interrupt(void)` etc.",
           "xc32": "XC32 ISRs: `void __ISR(_TIMER_1_VECTOR, IPL4SOFT) T1Handler(void)` with <sys/attribs.h>"}[dev.compiler]
    return f"""You are ChipLlama, an expert embedded firmware engineer for the Microchip {dev.name} ({dev.family}), compiled with {comp}.
Your code is VERIFIED afterwards: every `#pragma config` name/value and every REGbits.FIELD name is checked against Microchip's own device database (EDC), then the compiler builds it.

Rules:
- Reply with ONE complete, compilable C file in a single ```c fenced block, then at most 5 lines of explanation.
- `#include <xc.h>`. Use only register and bitfield names listed below or that exist in the device header; never invent them.
- Right after the includes, emit the complete `#pragma config` block given in the request, every line. Only change a value when the request requires it, and only to an allowed value.
- Define `#define _XTAL_FREQ <Hz>` (XC8) or `#define FOSC <Hz>UL` / `FCY`. {cyc}. Show the timer math with constant expressions and comments.
- Load period/count registers before starting a timer. Clear the interrupt flag, set priority, enable the interrupt, then start the timer. {isr}; clear the flag inside the ISR.
- The board is bare (no crystal): use the internal oscillator unless the request says otherwise. Include `main()` that initialises and loops forever."""


def relevant_sfrs(dev, spec=None, request: str = "", limit: int = 14) -> list:
    n = None
    if spec is not None:
        n = 3 if getattr(spec, "bits32", False) else spec.timer
    want = []
    keys = ["OSCCON", "OSCCON1", "OSCTUNE", "CLKDIV", "OSCFRQ", "INTCON", "INTCON0", "INTCON1", "INTCON2", "RCON"]
    if n:
        keys = [f"T{n}CON", f"TMR{n}", f"PR{n}", f"T{n}CONbits"] + keys
    for k in keys:
        r = dev.sfr(k)
        if r is not None and r not in want:
            want.append(r)
    if n:
        pats = (f"T{n}IF", f"T{n}IE", f"T{n}IP", f"TMR{n}IF", f"TMR{n}IE", f"TMR{n}IP")
        for r in dev.sfrs.values():
            if any(f.name in pats for f in r.fields) and r not in want:
                want.append(r)
    for tok in set(__import__("re").findall(r"\b[A-Z][A-Z0-9]{2,}\b", request.upper())):
        r = dev.sfr(tok)
        if r is not None and r not in want:
            want.append(r)
    return want[:limit]


def sfr_text(dev, regs) -> str:
    lines = [f"Register facts for {dev.name} (from Microchip EDC; bit positions exact):"]
    for r in regs:
        fs = ", ".join(f"{f.name}<{f.lsb + f.width - 1}:{f.lsb}>" if f.width > 1 else f"{f.name}<{f.lsb}>"
                       for f in r.fields if not (f.width == 1 and f.name[-1:].isdigit() and any(
                           g.width > 1 and f.name.startswith(g.name) for g in r.fields)))
        lines.append(f"- {r.name} @0x{r.address:X}" + (f": {fs}" if fs else " (data register)"))
    return "\n".join(lines)


def config_options_text(dev, max_vals: int = 10) -> str:
    lines = ["Configuration settings available on this device (name: allowed values):"]
    for s in dev.visible_settings():
        vals = s.allowed()
        txt = ", ".join(vals[:max_vals]) + (" ..." if len(vals) > max_vals else "")
        if s.range:
            txt = f"number {s.range[0]:#x}..{s.range[1]:#x}"
        lines.append(f"- {s.word}.{s.name}: {txt}")
    return "\n".join(lines)


def generic_user_prompt(request: str, spec, dev, ics: str = "PGx2") -> str:
    from . import pragmas
    s = [f"Request: {request}", "",
         f"Target: {dev.name} ({dev.family}, compiler {dev.compiler.upper()}). "
         + (f"Timer{spec.timer}, period {spec.period_s*1e3:.6g} ms, interrupt = {'yes' if spec.interrupt else 'no'}." if spec and spec.period_s else ""),
         "", "Emit exactly this configuration block (safe defaults for a bare board):", "```c",
         pragmas.render(dev, ics), "```", "", config_options_text(dev), "", sfr_text(dev, relevant_sfrs(dev, spec, request)),
         "", "Write the C file now."]
    return "\n".join(s)
