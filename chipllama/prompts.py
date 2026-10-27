from __future__ import annotations

from . import chipdb
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
- Do not emit configuration-bit pragmas (the user's MPLAB X project owns them) and do not invent registers. Include a `main()` that calls the init function and loops forever.
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


def first_user_prompt(request: str, spec: TimerSpec, solution_hint: TimerSolution | None, image_note: str) -> str:
    s = [f"Request: {request}", "", "Parameters extracted by the tool (verified later): " + spec_text(spec), "",
         register_table_text(spec)]
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
