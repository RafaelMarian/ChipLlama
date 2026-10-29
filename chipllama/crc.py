"""Programmable CRC generator (DS39897C sec. 21: CRCCON p.265, CRCXOR p.266).
PLEN<3:0> = polynomial length - 1; CRCXOR<15:1> = polynomial coefficients X15..X1 (X0 and the top term are implied)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

KNOWN = {"crc-16-ccitt": (16, 0x1021), "ccitt": (16, 0x1021), "crc-16": (16, 0x8005), "modbus": (16, 0x8005),
         "crc-8": (8, 0x07), "crc-7": (7, 0x09), "crc-5": (5, 0x05)}
_WORDS = re.compile(r"\bcrc", re.I)


@dataclass
class CrcSpec:
    length: int = 16
    poly: int = 0x1021
    notes: list[str] = field(default_factory=list)
    fcy: float = 16e6

    @property
    def xor(self) -> int:
        return self.poly & 0xFFFE

    def to_dict(self):
        return {"length": self.length, "poly": hex(self.poly), "PLEN": self.length - 1, "CRCXOR": hex(self.xor)}


def parse_request(text: str, fcy: float = 16e6, force: bool = False):
    if not force and not _WORDS.search(text):
        return None
    s = CrcSpec(fcy=fcy)
    t = text.lower()
    m = re.search(r"x\^?(\d+)(?:\s*\+\s*x\^?\d+|\s*\+\s*x)*\s*\+\s*1", t)
    if m:
        terms = [int(x) for x in re.findall(r"x\^?(\d+)", m.group(0))] + ([1] if re.search(r"\+\s*x(?!\^?\d)", m.group(0)) else [])
        s.length = max(terms)
        s.poly = sum(1 << e for e in terms if e < s.length) | 1
        return s
    m = re.search(r"poly(?:nomial)?\s*(?:=|:|of)?\s*(0x[0-9a-f]+)", t)
    for k, v in KNOWN.items():
        if k in t:
            s.length, s.poly = v
            break
    else:
        if not m:
            s.notes.append("no polynomial stated: assuming CRC-16-CCITT 0x1021")
    if m:
        s.poly = int(m.group(1), 16)
        ml = re.search(r"(\d{1,2})[- ]?bit", t)
        if ml:
            s.length = int(ml.group(1))
    if not 1 <= s.length <= 16:
        s.notes.append(f"{s.length}-bit CRC is outside the 16-bit hardware engine")
        s.length = 16
    return s


def calc_lines(spec):
    L = [f"CRC-{spec.length} polynomial 0x{spec.poly:X}",
         f"  CRCCONbits.PLEN = {spec.length - 1}  (length - 1, p.265)",
         f"  CRCXOR = 0x{spec.xor:04X}  (X<15:1>, bit 0 implied, p.266)",
         "  CRCWDAT = initial value; push data into CRCDAT (VWORD/CRCFUL flow control); CRCCONbits.CRCGO = 1; read CRCWDAT"]
    L += [f"  note: {x}" for x in spec.notes]
    return L, spec.to_dict()


def check(code, spec, rep, emu):
    g = emu.get
    if g("CRCCON", "PLEN") != spec.length - 1:
        rep.add("error", "CRC_PLEN", f"CRCCONbits.PLEN must be {spec.length - 1} (length - 1) for a {spec.length}-bit CRC (p.265).",
                spec.length - 1, g("CRCCON", "PLEN"))
    x = emu.regs.get("CRCXOR", 0) & 0xFFFE
    if x != spec.xor:
        rep.add("error", "CRC_XOR", f"CRCXOR = 0x{x:04X}, polynomial 0x{spec.poly:X} needs 0x{spec.xor:04X} (p.266).",
                hex(spec.xor), hex(x))
    if not re.search(r"CRCGO\s*=\s*1|CRCCON\s*\|=", code):
        rep.add("warning", "CRC_GO", "CRCCONbits.CRCGO is never set: the shifter never runs.")


def prompt_text(spec):
    return ("CRC facts (DS39897C sec. 21; verified later): CRCCON p.265 (CSIDL<13>, VWORD<12:8>, CRCFUL<7>, CRCMPT<6>, CRCGO<4>, PLEN<3:0>); "
            "CRCXOR p.266; data in CRCDAT, result in CRCWDAT.\n" + "\n".join(calc_lines(spec)[0]))


def pages(spec):
    return [263, 265, 266]


FINDING_PAGES = {"CRC_PLEN": [265], "CRC_XOR": [266], "CRC_GO": [265]}
