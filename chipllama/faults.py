"""DEMO fault injection (honest, clearly labelled): corrupt the model's FIRST attempt on purpose so the
self-correction loop (FAIL -> findings + datasheet pages -> fix -> VERIFIED) is visible in a live demo.

Nothing here pretends the model made the mistake: the agent logs/streams a `fault` event saying exactly what
was changed, and the result carries `demo_fault_injection`.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

DEMO_LABEL = "DEMO FAULT INJECTION"


@dataclass
class Fault:
    kind: str           # "uart_brg" | "timer_period" | "timer_prescale" | "config_fnosc"
    description: str
    original: str
    corrupted: str

    @property
    def diff(self) -> str:
        return "".join(difflib.unified_diff(self.original.splitlines(True), self.corrupted.splitlines(True),
                                            "model attempt 1", "after demo fault injection", n=1))

    def to_dict(self):
        return {"label": DEMO_LABEL, "kind": self.kind, "description": self.description, "diff": self.diff}


def _sub_first(pattern: str, repl, code: str):
    rx = re.compile(pattern, re.M)
    m = rx.search(code)
    if not m:
        return None
    return code[:m.start()] + repl(m) + code[m.end():]


def inject_fault(code: str, *, timer: int | None = None, uart: bool = False) -> Fault | None:
    """Return the corrupted code + description, or None if no known pattern was found."""
    if uart:
        new = _sub_first(r"^(\s*U1BRG\s*=\s*)([^;]+);", lambda m: f"{m.group(1)}({m.group(2).strip()}) * 2 + 1;", code)
        if new:
            return Fault("uart_brg", "U1BRG doubled (wrong baud-rate divisor -> ~half the requested baud)", code, new)
    if timer:
        pr = {23: "PR3", 45: "PR5"}.get(timer, f"PR{timer}")
        new = _sub_first(rf"^(\s*{pr}\s*=\s*)([^;]+);", lambda m: f"{m.group(1)}({m.group(2).strip()}) / 2;", code)
        if new:
            return Fault("timer_period", f"{pr} halved (timer period ~2x too short)", code, new)
        con = {23: "T2CON", 45: "T4CON"}.get(timer, f"T{timer}CON")
        new = _sub_first(rf"^(\s*{con}bits\.TCKPS\s*=\s*)([^;]+);",
                         lambda m: f"{m.group(1)}(({m.group(2).strip()}) + 1) & 3;", code)
        if new:
            return Fault("timer_prescale", f"{con}.TCKPS shifted to the next prescaler", code, new)
    new = _sub_first(r"^[ \t]*#pragma\s+config\s+FNOSC\s*=.*\n?", lambda m: "", code)
    if new:
        return Fault("config_fnosc", "`#pragma config FNOSC` line removed (oscillator selection missing)", code, new)
    return None
