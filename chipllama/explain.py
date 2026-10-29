"""Explain-code mode: annotate every SFR write/read in existing C with the register, the fields it touches
(decoded from the value when it is a constant) and the DS39897C page.  Deterministic; Muse can add a narrative."""
from __future__ import annotations

import re

from .chipdb import REGISTERS, REG_ALIASES, FIELD_ALIASES

_BITS = re.compile(r"\b([A-Z][A-Z0-9]{1,9})bits\.([A-Z][A-Z0-9]*)\s*(=|\|=|&=|\^=)?\s*([^;]*);?")
_REG = re.compile(r"\b([A-Z][A-Z0-9]{1,9})\s*(=|\|=|&=)\s*([^;=][^;]*);")
_PRAGMA = re.compile(r"#pragma\s+config\s+(\w+)\s*=\s*(\w+)")


def _num(s: str):
    s = s.strip().rstrip("uUlL")
    try:
        return int(s, 0) if not s.startswith("0b") else int(s[2:], 2)
    except ValueError:
        return None


def explain_code(code: str) -> dict:
    lines = []
    regs_used: dict[str, int] = {}
    for i, raw in enumerate(code.splitlines(), 1):
        line = raw.split("//")[0]
        notes = []
        if m := _PRAGMA.search(line):
            notes.append({"what": f"config bit {m.group(1)} = {m.group(2)}", "page": 288})
        for m in _BITS.finditer(line):
            r = REGISTERS.get(REG_ALIASES.get(m.group(1), m.group(1)))
            if not r:
                continue
            f = r.field(m.group(2))
            act = "writes" if m.group(3) else "reads"
            d = f"{r.name}.{m.group(2)} ({f.desc})" if f and f.desc else f"{r.name}.{m.group(2)}"
            notes.append({"what": f"{act} {d}" + (f" = {m.group(4).strip()}" if m.group(3) else ""), "page": r.page})
            regs_used[r.name] = r.page
        for m in _REG.finditer(line):
            name = REG_ALIASES.get(m.group(1), m.group(1))
            r = REGISTERS.get(name)
            if not r:
                continue
            v = _num(m.group(3))
            what = f"{m.group(2)} {r.name}"
            if v is not None and m.group(2) == "=":
                dec = {k: x for k, x in r.decode(v).items() if x}
                what += f" = 0x{v:04X}" + (": " + ", ".join(f"{k}={x}" for k, x in dec.items()) if dec else " (all fields 0)")
            else:
                what += f" {m.group(3).strip()}"
            notes.append({"what": what, "page": r.page})
            regs_used[r.name] = r.page
        if "__builtin_write_OSCCONL" in line:
            notes.append({"what": "OSCCONL unlock/lock sequence (IOLOCK gates PPS writes)", "page": 136})
        if re.search(r"__attribute__\s*\(\(\s*(__)?interrupt", line):
            m = re.search(r"\b(_\w+[Ii]nterrupt)\b", line)
            notes.append({"what": f"interrupt service routine {m.group(1) if m else ''}".strip(), "page": 84})
        if notes:
            lines.append({"line": i, "code": raw.strip(), "notes": notes})
    return {"lines": lines, "registers": [{"name": k, "page": v} for k, v in sorted(regs_used.items())]}


def explain_prompt(code: str, ann: dict) -> str:
    rows = "\n".join(f"L{a['line']}: " + "; ".join(f"{n['what']} (p.{n['page']})" for n in a["notes"]) for a in ann["lines"][:120])
    return ("Explain this PIC24FJ256GB110 C code to a developer new to the chip (datasheet DS39897C). The register facts "
            f"below are decoded deterministically - trust them, cite pages, do not invent registers:\n{rows}\n\n"
            "Give: 1) what the program does in 3-5 sentences, 2) a walkthrough by block (clock, pins, peripherals, "
            "interrupts, main loop), 3) anything suspicious. Be concise.\n\n" + code[:8000])
