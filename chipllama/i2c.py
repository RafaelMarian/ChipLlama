"""I2C master (DS39897C sec. 17 "I2C": I2CxCON p.194, I2CxSTAT p.196, Table 16-1 / Eq. 16-1 p.193).

    I2CxBRG = Fcy/Fscl - Fcy/10,000,000 - 1      (16 MHz: 100 kHz -> 157, 400 kHz -> 37, 1 MHz -> 13)
Pins are fixed (not PPS) and open-drain; on the 100-pin PIC24FJ256GB110 (pin table p.18-20):
    SCL1 = RA14 (pin 66), SDA1 = RA15 (pin 67); SCL2 = RA2 (58), SDA2 = RA3 (59); SCL3 = RE6 (4), SDA3 = RE7 (5).
(RG2/RG3 are the USB D+/D- pins on this device.)  External pull-ups are required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PINS = {1: ("RA14", "RA15", 66, 67), 2: ("RA2", "RA3", 58, 59), 3: ("RE6", "RE7", 4, 5)}
_WORDS = re.compile(r"\b(i2c\d?|i²c|twi|iic)\b", re.I)


@dataclass
class I2cSpec:
    n: int = 1
    freq_hz: float = 100e3
    fcy: float = 16e6
    address: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self):
        return {"n": self.n, "freq_hz": self.freq_hz, "fcy": self.fcy, "address": self.address,
                "scl": PINS[self.n][0], "sda": PINS[self.n][1]}


def brg_for(fcy: float, fscl: float) -> int:
    return int(round(fcy / fscl - fcy / 1e7 - 1))


def fscl_for(fcy: float, brg: int) -> float:
    return fcy / (brg + 1 + fcy / 1e7)


def parse_request(text: str, fcy: float = 16e6, force: bool = False) -> I2cSpec | None:
    if not force and not _WORDS.search(text):
        return None
    s = I2cSpec(fcy=fcy)
    m = re.search(r"\bi2c\s*([1-3])\b", text, re.I)
    if m:
        s.n = int(m.group(1))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(k|M)hz\b", text, re.I):
        f = float(m.group(1)) * (1e3 if m.group(2).lower() == "k" else 1e6)
        if f <= 1e6:
            s.freq_hz = f
            break
    else:
        if re.search(r"fast[- ]mode\s*plus|fm\+", text, re.I):
            s.freq_hz = 1e6
        elif re.search(r"fast[- ]mode", text, re.I):
            s.freq_hz = 400e3
        else:
            s.notes.append("no I2C clock stated: assuming 100 kHz (standard mode)")
    m = re.search(r"address\s*(?:of\s*)?(0x[0-9a-f]{1,2}|\d{1,3})", text, re.I)
    if m:
        s.address = int(m.group(1), 0)
    if re.search(r"\bslave\b", text, re.I):
        s.notes.append("I2C slave requested: the checker verifies master mode only")
    return s


def calc_lines(spec: I2cSpec):
    brg = brg_for(spec.fcy, spec.freq_hz)
    act = fscl_for(spec.fcy, brg)
    n = spec.n
    scl, sda, ps, pd = PINS[n]
    L = [f"I2C{n} master: Fscl {spec.freq_hz/1e3:g} kHz at Fcy={spec.fcy/1e6:g} MHz",
         f"  I2C{n}BRG = Fcy/Fscl - Fcy/10MHz - 1 = {spec.fcy/spec.freq_hz:g} - {spec.fcy/1e7:g} - 1 -> {brg} "
         f"(actual {act/1e3:.4g} kHz, Eq. 16-1 / Table 16-1 p.193)",
         f"  I2C{n}CON = 0x{(1 << 15) | ((spec.freq_hz <= 100e3) << 9):04X}  (I2CEN=1{', DISSLW=1 for 100 kHz' if spec.freq_hz <= 100e3 else ', DISSLW=0 slew control for 400 kHz'})",
         f"  pins (fixed, open-drain, 100-pin): SCL{n} = {scl} (pin {ps}), SDA{n} = {sda} (pin {pd}); external pull-ups (2.2k-4.7k) to VDD"]
    if spec.address is not None:
        L.append(f"  7-bit address 0x{spec.address:02X}: write byte 0x{spec.address << 1:02X}, read byte 0x{(spec.address << 1) | 1:02X}")
    L += [f"  note: {x}" for x in spec.notes]
    return L, {"I2CxBRG": brg, "fscl": act, "spec": spec.to_dict()}


def check(code: str, spec: I2cSpec, rep, emu) -> None:
    n = spec.n
    con, brg_r = f"I2C{n}CON", f"I2C{n}BRG"
    g = emu.get
    exp = brg_for(spec.fcy, spec.freq_hz)
    brg = emu.regs.get(brg_r, 0)
    rep.registers[con] = emu.regs.get(con, 0)
    rep.expected[brg_r] = exp
    if g(con, "I2CEN") != 1:
        rep.add("error", "I2C_EN", f"{con}bits.I2CEN is not set: I2C{n} is off (p.194).", 1, 0)
    if not any(e.target.split(".")[0] == brg_r for e in emu.events):
        rep.add("error", "I2C_BRG", f"{brg_r} is never written. For {spec.freq_hz/1e3:g} kHz: {brg_r} = {exp} (p.193).", exp, "unset")
    elif brg < 2:
        rep.add("error", "I2C_BRG", f"{brg_r} = {brg}: values 0 and 1 are not allowed (p.193).", exp, brg)
    else:
        act = fscl_for(spec.fcy, brg)
        if abs(act - spec.freq_hz) / spec.freq_hz > 0.05 or act > spec.freq_hz * 1.02:
            rep.add("error", "I2C_BRG", f"{brg_r} = {brg} gives Fscl = {act/1e3:.4g} kHz, request {spec.freq_hz/1e3:g} kHz. "
                    f"I2CxBRG = Fcy/Fscl - Fcy/10,000,000 - 1 = {exp} (Eq. 16-1, p.193).", exp, brg)
        else:
            rep.add("info", "I2C_FSCL", f"{brg_r} = {brg} -> Fscl = {act/1e3:.4g} kHz")
    on = next((i for i, e in enumerate(emu.events) if e.target.split(".")[0] == con and (e.after >> 15) & 1), None)
    bi = next((i for i, e in enumerate(emu.events) if e.target.split(".")[0] == brg_r), None)
    if on is not None and bi is not None and bi > on:
        rep.add("warning", "I2C_ORDER", f"{brg_r} is written after I2CEN=1; load the baud generator before enabling.")
    if spec.freq_hz <= 100e3 and g(con, "DISSLW") != 1:
        rep.add("info", "I2C_DISSLW", f"{con}bits.DISSLW = 0: slew-rate control is meant for 400 kHz; set DISSLW = 1 at 100 kHz.")
    scl, sda = PINS[n][:2]
    for pin in (scl, sda):
        if (emu.regs.get(f"ODC{pin[1]}", 0) >> int(pin[2:])) & 1:
            rep.add("info", "I2C_ODC", f"ODC on {pin} is unnecessary: the I2C module drives its pins open-drain itself.")
    mapped = [k for k in emu.regs if k.startswith("RPINR")]
    from .uart import PIN_RP
    for pin in (scl, sda):
        rp = PIN_RP.get(pin)
        if rp is None:
            continue
        users = [e.target for e in emu.events if e.target.startswith(("RPINR", "RPOR")) and _maps(emu, e.target, rp)]
        if users:
            rep.add("error", "I2C_PIN_CONFLICT", f"{pin} is SCL/SDA of I2C{n} but is also mapped by PPS ({users[0]}).")
    del mapped
    if re.search(rf"I2C{n}CONbits\.SEN\s*=\s*1", code) and not re.search(rf"while\s*\(\s*I2C{n}CONbits\.SEN", code):
        rep.add("warning", "I2C_SEN_WAIT", f"Start condition: wait for I2C{n}CONbits.SEN to clear before writing I2C{n}TRN.")
    if re.search(rf"I2C{n}TRN\s*=", code) and not re.search(rf"I2C{n}STATbits\.(TRSTAT|TBF|ACKSTAT)", code):
        rep.add("warning", "I2C_TX_WAIT", f"I2C{n}TRN is written without waiting on I2C{n}STATbits.TRSTAT/TBF or checking ACKSTAT.")


def _odc_regs():
    from . import chipdb
    return chipdb.REGISTERS


def _maps(emu, target: str, rp: int) -> bool:
    reg, _, fld = target.partition(".")
    if reg.startswith("RPOR"):
        return fld == f"RP{rp}R" and emu.get(reg, fld) != 0
    return bool(fld) and emu.get(reg, fld) == rp


def prompt_text(spec: I2cSpec) -> str:
    lines, _ = calc_lines(spec)
    n = spec.n
    return (f"I2C facts (DS39897C; verified later): I2C{n}CON p.194 (I2CEN<15>, SCLREL<12>, DISSLW<9>, ACKDT<5>, ACKEN<4>, "
            f"RCEN<3>, PEN<2>, RSEN<1>, SEN<0>); I2C{n}STAT p.196 (ACKSTAT<15>, TRSTAT<14>, BCL<10>, IWCOL<7>, TBF<0>, RBF<1>). "
            f"I2C{n}BRG = Fcy/Fscl - Fcy/10,000,000 - 1 (p.193). Pins are fixed (not PPS). Master sequence: SEN=1, wait SEN==0; "
            f"I2C{n}TRN = addr<<1; wait TRSTAT==0; check ACKSTAT; ...; PEN=1, wait PEN==0. Reads: RCEN=1, wait RBF, "
            f"read I2C{n}RCV, then ACKDT/ACKEN.\n" + "\n".join(lines))


def pages(spec: I2cSpec) -> list[int]:
    return [191, 193, 194, 196]


FINDING_PAGES = {"I2C_EN": [194], "I2C_BRG": [193], "I2C_ORDER": [193], "I2C_DISSLW": [194], "I2C_ODC": [134],
                 "I2C_PIN_CONFLICT": [138], "I2C_SEN_WAIT": [194], "I2C_TX_WAIT": [196], "I2C_FSCL": [193]}
