"""Interrupt vector facts for the PIC24FJ256GB110 (DS39897C sec. 7, Table 7-2 p.84, IFS/IEC/IPC p.85-110).

IRQ n  ->  flag IFS(n//16) bit n%16, enable IEC(n//16) bit n%16, priority IPC(n//4) bits (n%4)*4 .. +2.
The vector table (name -> IRQ) is generated from the Microchip EDC (gb110_tables.VECTORS).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from . import chipdb
from .cemu import isr_clears_flag

ISR_PAGE = 84


@dataclass(frozen=True)
class Vector:
    name: str       # "CNInterrupt" (C function is "_CNInterrupt")
    irq: int
    ifs: tuple[str, str]
    iec: tuple[str, str]
    ipc: tuple[str, str]

    @property
    def isr(self) -> str:
        return "_" + self.name

    @property
    def pages(self) -> list[int]:
        return [ISR_PAGE] + [chipdb.REGISTERS[r].page for r in (self.ifs[0], self.iec[0], self.ipc[0])]


def _field_at(reg: str, lsb: int, suffix: str) -> str:
    fs = [f for f in chipdb.REGISTERS[reg].fields if f.lsb == lsb]
    for f in fs:
        if f.name.endswith(suffix):
            return f.name
    return fs[0].name


def vector(name: str) -> Vector:
    name = name.lstrip("_")
    if not name.endswith("Interrupt"):
        name += "Interrupt"
    if name.startswith("Alt"):
        name = name[3:]
    n = chipdb.VECTORS[name]
    ifs, iec, ipc = f"IFS{n // 16}", f"IEC{n // 16}", f"IPC{n // 4}"
    return Vector(name, n, (ifs, _field_at(ifs, n % 16, "IF")), (iec, _field_at(iec, n % 16, "IE")),
                  (ipc, _field_at(ipc, (n % 4) * 4, "IP")))


def suggest(name: str) -> str | None:
    up = {k.upper(): k for k in chipdb.VECTORS}
    n = name.lstrip("_").upper()
    if n in up:
        return "_" + up[n]
    m = difflib.get_close_matches(n, list(up), n=1, cutoff=0.6)
    if m:
        return "_" + up[m[0]]
    m = difflib.get_close_matches(name.lstrip("_"), list(chipdb.VECTORS), n=1, cutoff=0.6)
    return "_" + m[0] if m else None


def check_vector(emu, rep, v: Vector, *, priority: int | None = None, what: str = "") -> None:
    """Standard interrupt rules: IE set, IP 1..7 (or the requested one), ISR exists with attribute and clears the flag."""
    what = what or v.name.replace("Interrupt", "")
    ie = emu.get(*v.iec)
    ip = emu.get(*v.ipc)
    if ie != 1:
        rep.add("error", "IE", f"{v.iec[0]}.{v.iec[1]} is not set: the {what} interrupt never fires.", 1, ie)
    if ip == 0:
        rep.add("error", "IP", f"{v.ipc[0]}.{v.ipc[1]}=0 disables the {what} interrupt (priority 1-7 needed).", "1..7", ip)
    elif priority and ip != priority:
        rep.add("error", "IP", f"{v.ipc[1]} priority differs from request.", priority, ip)
    isr = next((f for f in emu.functions if f.name == v.isr), None)
    if isr is None:
        rep.add("error", "ISR", f"no ISR named {v.isr} found (XC16 vector name, DS39897C Table 7-2 p.84).")
        return
    if not isr.is_isr:
        rep.add("error", "ISR_ATTR", f"{v.isr} lacks the interrupt attribute (e.g. __attribute__((interrupt, auto_psv))).")
    if not isr_clears_flag(emu, isr, v.ifs):
        rep.add("error", "ISR_FLAG", f"{v.isr} does not clear {v.ifs[0]}.{v.ifs[1]}; it would re-enter forever.")


def prompt_line(v: Vector, priority: int | None = None) -> str:
    return (f"{v.name.replace('Interrupt', '')} interrupt (IRQ {v.irq}): flag {v.ifs[0]}bits.{v.ifs[1]}, "
            f"enable {v.iec[0]}bits.{v.iec[1]}, priority {v.ipc[0]}bits.{v.ipc[1]}"
            f"{' = %d' % priority if priority else ' (1..7, 0 = disabled)'}, handler "
            f"void __attribute__((interrupt, auto_psv)) {v.isr}(void) that clears the flag.")
