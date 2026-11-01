"""Parallel Master Port, basic master mode (DS39897C sec. 19: PMCON p.242, PMMODE p.244, PMAEN p.245)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORDS = re.compile(r"\b(pmp|parallel master|parallel port)\b", re.I)


@dataclass
class PmpSpec:
    addr_lines: int = 0
    bits16: bool = False
    cs1: bool = False
    master_mode: int = 2          # MODE: 10 = Master mode 2 (PMRD/PMWR), 11 = Master mode 1 (PMRD/W + PMENB)
    notes: list[str] = field(default_factory=list)
    fcy: float = 16e6

    def to_dict(self):
        return {"addr_lines": self.addr_lines, "bits16": self.bits16, "cs1": self.cs1, "MODE": self.master_mode}

    @property
    def pmaen(self) -> int:
        return ((1 << self.addr_lines) - 1) | ((1 << 14) if self.cs1 else 0)


def parse_request(text, fcy=16e6, force=False):
    if not force and not _WORDS.search(text):
        return None
    s = PmpSpec(fcy=fcy)
    m = re.search(r"(\d{1,2})\s*address", text, re.I)
    if m:
        s.addr_lines = min(14, int(m.group(1)))
    s.bits16 = bool(re.search(r"16[- ]?bit", text))
    s.cs1 = bool(re.search(r"\bcs1?\b|chip select", text, re.I))
    if re.search(r"master\s*mode\s*1|PMENB", text, re.I):
        s.master_mode = 3
    return s


def calc_lines(spec):
    con = (1 << 15) | (1 << 9) | (1 << 8) | ((2 << 6) if spec.cs1 else 0)
    mode = (spec.master_mode << 8) | (int(spec.bits16) << 10)
    L = [f"PMP master mode {1 if spec.master_mode == 3 else 2}, {16 if spec.bits16 else 8}-bit",
         f"  PMMODE = 0x{mode:04X}  (MODE={spec.master_mode:02b}{', MODE16=1' if spec.bits16 else ''}; add WAITB/WAITM/WAITE for slow devices)",
         f"  PMAEN  = 0x{spec.pmaen:04X}  (PMA0..PMA{spec.addr_lines - 1 if spec.addr_lines else 0}{', PMCS1 on PTEN14' if spec.cs1 else ''})",
         f"  PMCON  = 0x{con:04X}  (PMPEN=1 last, PTWREN=1, PTRDEN=1{', CSF=10 CS1 enabled' if spec.cs1 else ''})",
         "  write: while(PMMODEbits.BUSY); PMDIN1 = data;   read: dummy = PMDIN1; while(PMMODEbits.BUSY); value = PMDIN1;"]
    L += [f"  note: {x}" for x in spec.notes]
    return L, dict(spec.to_dict(), PMCON=con, PMMODE=mode, PMAEN=spec.pmaen)


def check(code, spec, rep, emu):
    g = emu.get
    if g("PMCON", "PMPEN") != 1:
        rep.add("error", "PMP_EN", "PMCONbits.PMPEN is not set: the PMP is off (p.242).", 1, 0)
    if g("PMMODE", "MODE") not in (2, 3):
        rep.add("error", "PMP_MODE", "PMMODEbits.MODE must be 10 or 11 (master) (p.244).", spec.master_mode, g("PMMODE", "MODE"))
    if g("PMMODE", "MODE16") != int(spec.bits16):
        rep.add("error", "PMP_WIDTH", f"PMMODEbits.MODE16 should be {int(spec.bits16)}.", int(spec.bits16), g("PMMODE", "MODE16"))
    if not g("PMCON", "PTWREN") or not g("PMCON", "PTRDEN"):
        rep.add("error", "PMP_STROBES", "PMCON: PTWREN and PTRDEN must be 1 to drive PMWR/PMRD (p.242).")
    pmaen = emu.regs.get("PMAEN", 0)
    if pmaen & spec.pmaen != spec.pmaen:
        rep.add("error", "PMP_PMAEN", f"PMAEN = 0x{pmaen:04X} must enable 0x{spec.pmaen:04X} (address lines / CS1, p.245).",
                hex(spec.pmaen), hex(pmaen))
    if re.search(r"PMDIN1\s*=", code) and "BUSY" not in code:
        rep.add("warning", "PMP_BUSY", "PMDIN1 is written without waiting on PMMODEbits.BUSY.")


def prompt_text(spec):
    return ("PMP facts (DS39897C sec. 19; verified later): PMCON p.242 (PMPEN<15>, ADRMUX<12:11>, PTWREN<9>, PTRDEN<8>, CSF<7:6>); "
            "PMMODE p.244 (BUSY<15>, IRQM<14:13>, INCM<12:11>, MODE16<10>, MODE<9:8>, WAITB/WAITM/WAITE); PMAEN p.245 (PTEN<15:0>).\n"
            + "\n".join(calc_lines(spec)[0]))


def pages(spec):
    return [241, 242, 244, 245]


FINDING_PAGES = {"PMP_EN": [242], "PMP_MODE": [244], "PMP_WIDTH": [244], "PMP_STROBES": [242], "PMP_PMAEN": [245], "PMP_BUSY": [244]}
