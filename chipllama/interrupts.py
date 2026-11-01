"""Interrupt controller: vector names vs. the IVT (DS39897C Table 7-2 p.84), IFS/IEC/IPC bits (p.85-110),
external INT0-4 edge (INTCON2 p.83) and INT1-4 PPS inputs (RPINR0-2 p.141-142), and a whole-file ISR audit."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import chipdb
from .irq import Vector, vector, check_vector, prompt_line, suggest, ISR_PAGE

# sources owned by other modules (timer / UART / GPIO CN / ADC / OC / IC checkers handle them)
_OWNED = re.compile(r"^(T\d|CN|ADC1|OC\d|IC\d|U1RX|U1TX)Interrupt$")
TRAPS = {"_DefaultInterrupt", "_OscillatorFail", "_AddressError", "_StackError", "_MathError",
         "_AltOscillatorFail", "_AltAddressError", "_AltStackError", "_AltMathError", "_ReservedTrap0",
         "_ReservedTrap5", "_ReservedTrap6", "_ReservedTrap7"}
_ALIASES = {"ADC": "ADC1", "AD1": "ADC1", "COMPARATOR": "Comp", "CMP": "Comp", "USB": "USB1", "RTC": "RTCC",
            "PMP": "PMP", "CRC": "CRC", "LVD": "LVD", "CTMU": "CTMU"}
_SRC = re.compile(r"\b(INT[0-4]|U[1-4](?:RX|TX|ERR)|SPI[1-3](?:ERR)?|[MS]I2C[1-3]|I2C[1-3]|T[1-5]|TMR[1-5]|CN|"
                  r"ADC1?|AD1|OC[1-9]|IC[1-9]|RTCC?|USB1?|PMP|CRC|LVD|CTMU|COMP(?:ARATOR)?|CMP)\b", re.I)
_EXT = re.compile(r"\bexternal interrupt\s*(\d)?", re.I)


@dataclass
class IrqReq:
    vec: Vector
    priority: int | None = None
    edge: str | None = None           # "rising" | "falling" for INTx
    pin_rp: int | None = None         # INT1-4 remappable input

    @property
    def ext(self) -> int | None:
        m = re.fullmatch(r"INT(\d)Interrupt", self.vec.name)
        return int(m.group(1)) if m else None

    def to_dict(self):
        return {"vector": self.vec.name, "isr": self.vec.isr, "irq": self.vec.irq, "priority": self.priority,
                "edge": self.edge, "pin_rp": self.pin_rp, "ifs": self.vec.ifs, "iec": self.vec.iec, "ipc": self.vec.ipc}


@dataclass
class IrqSpec:
    reqs: list[IrqReq] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self):
        return {"interrupts": [r.to_dict() for r in self.reqs]}


def _vec_name(tok: str) -> str | None:
    t = tok.upper()
    t = {"TMR": "T"}.get(t[:3], t[:3]) + t[3:] if t.startswith("TMR") else t
    t = _ALIASES.get(t, t)
    if t in ("I2C1", "I2C2", "I2C3"):
        t = "MI2C" + t[-1]
    if t.endswith("ERR"):
        t = t[:-3] + "Err"
    if t.startswith("COMP"):
        t = "Comp"
    name = t + "Interrupt"
    return name if name in chipdb.VECTORS else None


def parse_request(text: str, fcy: float = 16e6, force: bool = False) -> IrqSpec | None:
    if not force and not re.search(r"\binterrupts?\b|\bISR\b", text, re.I):
        return None
    spec = IrqSpec()
    seen = set()
    toks = [m.group(1) for m in _SRC.finditer(text)]
    for m in ([] if any(t.upper().startswith("INT") for t in toks) else _EXT.finditer(text)):
        toks.append(f"INT{m.group(1) or 0}")
    for t in toks:
        n = _vec_name(t)
        if n is None or n in seen or (not force and _OWNED.match(n)):
            continue
        seen.add(n)
        spec.reqs.append(IrqReq(vector(n)))
    if not spec.reqs:
        return spec if force else None
    pr = re.search(r"priority\s*(?:level\s*)?(\d)", text, re.I)
    for r in spec.reqs:
        if pr:
            r.priority = int(pr.group(1))
        if r.ext is not None:
            if re.search(r"falling|negative|high[- ]to[- ]low", text, re.I):
                r.edge = "falling"
            elif re.search(r"rising|positive|low[- ]to[- ]high", text, re.I):
                r.edge = "rising"
            if r.ext:
                from .uart import _PIN, _pin_to_rp
                m = re.search(rf"\bINT{r.ext}\b[^,;.]*?\b(?:on|pin|at|=|->)?\s*{_PIN}\b", text, re.I) or \
                    re.search(rf"{_PIN}\b[^,;.]*?\bINT{r.ext}\b", text, re.I)
                if m:
                    r.pin_rp = _pin_to_rp(m.group(1))
                else:
                    spec.notes.append(f"INT{r.ext} is a remappable input: state its pin (RPn) so RPINR can be verified")
    return spec


def _int_pps(n: int) -> tuple[str, str]:
    return chipdb.FIELD_ALIASES[f"INT{n}R"]


def calc_lines(spec: IrqSpec):
    lines, d = [], {"interrupts": []}
    for r in spec.reqs:
        v = r.vec
        lines.append(prompt_line(v, r.priority))
        lines.append(f"  IRQ {v.irq}: {v.ifs[0]}<{v.irq % 16}>, {v.iec[0]}<{v.irq % 16}>, "
                     f"{v.ipc[0]}<{(v.irq % 4) * 4 + 2}:{(v.irq % 4) * 4}> (pages {', '.join('p.%d' % p for p in v.pages)})")
        if r.ext is not None:
            lines.append(f"  INTCON2bits.INT{r.ext}EP = {1 if r.edge == 'falling' else 0} "
                         f"({'falling' if r.edge == 'falling' else 'rising'} edge{'' if r.edge else ', default'})")
            if r.ext == 0:
                lines.append("  INT0 is fixed on RF6 (not remappable); TRISFbits.TRISF6 = 1")
            else:
                reg, f = _int_pps(r.ext)
                lines.append(f"  PPS input: {reg}bits.{f} = {r.pin_rp if r.pin_rp is not None else '<RPn>'} "
                             "(unlock IOLOCK first); pin TRIS = 1 and digital")
        d["interrupts"].append(r.to_dict())
    lines += [f"  note: {n}" for n in spec.notes]
    return lines, d


def check(code: str, spec: IrqSpec, rep, emu) -> None:
    from .uart import RP_PIN
    for r in spec.reqs:
        check_vector(emu, rep, r.vec, priority=r.priority)
        if r.ext is None:
            continue
        ep = emu.get("INTCON2", f"INT{r.ext}EP")
        if r.edge and ep != (1 if r.edge == "falling" else 0):
            rep.add("error", "INT_EDGE", f"INTCON2bits.INT{r.ext}EP must be {1 if r.edge == 'falling' else 0} "
                    f"for a {r.edge} edge (p.83).", 1 if r.edge == "falling" else 0, ep)
        if r.ext == 0:
            pin = "RF6"
        else:
            reg, f = _int_pps(r.ext)
            got = emu.get(reg, f)
            if r.pin_rp is not None and got != r.pin_rp:
                rep.add("error", "INT_PPS", f"INT{r.ext} must be mapped with {reg}bits.{f} = {r.pin_rp} (p.141).", r.pin_rp, got)
            pin = RP_PIN.get(got if r.pin_rp is None else r.pin_rp)
        if pin:
            tr, tb = f"TRIS{pin[1]}", f"TRIS{pin[1]}{pin[2:]}"
            if emu.get(tr, tb) != 1:
                rep.add("error", "INT_TRIS", f"INT{r.ext} pin {pin} must be an input ({tr}bits.{tb}=1).", 1, emu.get(tr, tb))
            if pin.startswith("RB") and emu.get("AD1PCFGL", f"PCFG{pin[2:]}") != 1:
                rep.add("error", "INT_ANALOG", f"INT{r.ext} pin {pin} is AN{pin[2:]}: set AD1PCFGLbits.PCFG{pin[2:]}=1.", 1, 0)


def audit(code: str, rep, emu) -> None:
    """Whole-file ISR sanity, run for every request: handler names must be real vectors, every enabled
    interrupt needs a handler, handlers need the interrupt attribute."""
    names = {f.name for f in emu.functions}
    for fn in emu.functions:
        nm = fn.name
        looks_isr = fn.is_isr or re.fullmatch(r"_(?:Alt)?\w+Interrupt", nm)
        if not looks_isr or nm in TRAPS:
            continue
        base = re.sub(r"^_(?:Alt)?", "", nm)
        if base not in chipdb.VECTORS:
            sug = suggest(base)
            rep.add("error", "ISR_NAME", f"{nm} is not a PIC24FJ256GB110 interrupt vector (Table 7-2 p.84); it would never "
                    "run and XC16 only warns." + (f" Did you mean {sug}?" if sug else ""))
        elif not fn.is_isr:
            if not any(f.code == "ISR_ATTR" for f in rep.findings):
                rep.add("error", "ISR_ATTR", f"{nm} lacks __attribute__((interrupt, auto_psv)): it compiles as a normal "
                        "function and the vector stays on the default trap.")
    have = {f.code + f.message[:60] for f in rep.findings}
    for vname, n in chipdb.VECTORS.items():
        try:
            v = vector(vname)
        except Exception:      # pragma: no cover
            continue
        if emu.get(*v.iec) == 1 and v.isr not in names and f"_Alt{vname}" not in names:
            if not any(v.isr in h for h in have):
                rep.add("error", "IE_NO_ISR", f"{v.iec[0]}.{v.iec[1]} is enabled but no {v.isr} exists: the CPU "
                        "jumps to _DefaultInterrupt (reset loop).")


def prompt_text(spec: IrqSpec) -> str:
    lines, _ = calc_lines(spec)
    return ("Interrupt facts (DS39897C sec. 7; verified later): handler names must match the XC16 vector names "
            "exactly; IPCx priority 0 disables the source; clear the IFSx flag inside the handler.\n" + "\n".join(lines))


def pages(spec: IrqSpec) -> list[int]:
    out = [ISR_PAGE]
    for r in spec.reqs:
        out += r.vec.pages[1:]
        if r.ext is not None:
            out.append(83)
            if r.ext:
                out.append(141)
    return list(dict.fromkeys(out))


FINDING_PAGES = {"ISR_NAME": [84], "IE_NO_ISR": [84], "INT_EDGE": [83], "INT_PPS": [141], "INT_TRIS": [133],
                 "INT_ANALOG": [273], "ISR_ATTR": [84]}
