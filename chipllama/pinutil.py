"""Shared PPS / port helpers for the batch-2 peripheral checkers (DS39897C sec. 10.4, Table 10-3 p.138)."""
from __future__ import annotations

import re

from .uart import OUTPUT_RP_MAX, rpor_field, port_pin, rp_name, analog_channel, _PIN, _pin_to_rp


def find_pin(text: str, *roles: str) -> int | None:
    """RP number for '<role> on RPn/Rxn' or 'Rxn as <role>' (role is a regex alternative)."""
    r = "(?:" + "|".join(roles) + ")"
    for p in (rf"\b{r}\b\s*(?:pin\s*)?(?:on|=|:|->|to|at|@)?\s*(?:pin\s*)?{_PIN}\b",
              rf"\b{_PIN}\s*(?:\(|as|for|=|:)\s*{r}\b"):
        m = re.search(p, text, re.I)
        if m:
            return _pin_to_rp(m.group(1))
    return None


def mapped_outputs(emu, fn: int) -> list[int]:
    return [rp for rp in range(OUTPUT_RP_MAX + 1) if emu.get(*rpor_field(rp)) == fn]


def check_output(emu, rep, fn: int, rp: int | None, label: str, code: str, *, required: bool = True) -> int | None:
    """PPS output `label` (function number fn) on rp. Returns the RP actually used (or None)."""
    mapped = mapped_outputs(emu, fn)
    if rp is not None:
        if rp > OUTPUT_RP_MAX:
            rep.add("error", code, f"{rp_name(rp)} is input-only; {label} is an output and needs an RP0..RP31 pin.")
            return None
        if rp not in mapped:
            reg, f = rpor_field(rp)
            rep.add("error", code, f"{label} must be mapped: {reg}bits.{f} = {fn} (Table 10-3, p.138).", fn, emu.get(reg, f))
        return rp
    if not mapped and required:
        rep.add("error", code, f"{label} (output function {fn}) is not mapped to any RPn pin (RPORx.RPnR = {fn}).")
    return mapped[0] if mapped else None


def check_input(emu, rep, reg: str, field: str, rp: int | None, label: str, code: str, *, required: bool = True):
    v = emu.get(reg, field)
    if rp is not None:
        if v != rp:
            rep.add("error", code, f"{reg}bits.{field} = {v}, expected {rp} to read {label} from {rp_name(rp)}"
                    f"/{port_pin(rp)}.", rp, v)
        return rp
    if v == 0x3F and required:
        rep.add("error", code, f"{label} input is not mapped: set {reg}bits.{field} = <RPn number>.")
    return None if v == 0x3F else v


def tris_check(emu, rep, rp: int | None, want: int, label: str, code: str, sev="warning"):
    pin = port_pin(rp) if rp is not None else None
    if not pin:
        return
    t = emu.get(f"TRIS{pin[1]}", f"TRIS{pin[1]}{pin[2:]}")
    if t != want:
        rep.add(sev, code, f"{label} pin {pin}: TRIS{pin[1]}bits.TRIS{pin[1]}{pin[2:]} should be {want} "
                f"({'input' if want else 'output'}).", want, t)
    an = analog_channel(pin)
    if an is not None and want == 1 and not (emu.regs.get("AD1PCFGL", 0) >> an) & 1:
        rep.add("error", "ANALOG_PIN", f"{label} pin {pin} is also AN{an} and resets analog: set "
                f"AD1PCFGLbits.PCFG{an} = 1 (p.273), or the digital input reads 0.", 1, 0)


def pps_lock_warn(emu, rep, code: str):
    if any(e.target.startswith(("RPOR", "RPINR")) for e in emu.events) and \
            not re.search(r"__builtin_write_OSCCONL|OSCCONbits\.IOLOCK", code):
        rep.add("warning", "PPS_UNLOCK", "PPS registers are written without the IOLOCK unlock sequence "
                "__builtin_write_OSCCONL(OSCCON & 0xBF) (works only because IOLOCK resets to 0).")


def wrote(emu, reg: str) -> bool:
    return any(e.target.split(".")[0] == reg for e in emu.events)


def first_event(emu, reg: str, bit: int):
    return next((i for i, e in enumerate(emu.events) if e.target.split(".")[0] == reg and (e.after >> bit) & 1), None)


def pin_line(rp: int | None, fn_desc: str) -> str:
    if rp is None:
        return ""
    pin = port_pin(rp)
    return f"{rp_name(rp)}{'/' + pin if pin else ''}"
