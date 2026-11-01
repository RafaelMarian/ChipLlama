"""RTCC (DS39897C sec. 20: RCFGCAL p.253, ALCFGRPT p.255, calibration Eq. 20-1 p.260).
RTCEN and RTCVAL writes need RCFGCAL.RTCWREN=1, set via the NVMKEY unlock: __builtin_write_RTCWEN().
The RTCC is clocked by the 32.768 kHz SOSC: OSCCON.SOSCEN must be set (__builtin_write_OSCCONL(OSCCON | 0x02))."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

AMASK = {"half": 0, "second": 1, "10s": 2, "minute": 3, "10min": 4, "hour": 5, "day": 6, "week": 7, "month": 8, "year": 9}
_WORDS = re.compile(r"\b(rtcc|real[- ]?time clock|calendar|rtc)\b", re.I)


@dataclass
class RtccSpec:
    alarm: str | None = None
    notes: list[str] = field(default_factory=list)
    fcy: float = 16e6

    def to_dict(self):
        return {"alarm": self.alarm, "amask": AMASK.get(self.alarm) if self.alarm else None}


def parse_request(text: str, fcy: float = 16e6, force: bool = False):
    if not force and not _WORDS.search(text):
        return None
    s = RtccSpec(fcy=fcy)
    m = re.search(r"(?:alarm|interrupt|wake)\D{0,20}every\s*(half|10\s*s|10\s*min|second|minute|hour|day|week|month|year)", text, re.I) \
        or re.search(r"every\s*(half[- ]second|10\s*s|10\s*min|second|minute|hour|day|week|month|year)\D{0,20}alarm", text, re.I)
    if m:
        k = m.group(1).lower().replace(" ", "")
        s.alarm = {"10s": "10s", "10min": "10min"}.get(k, k.split("-")[0])
    s.notes.append("RTCC needs a 32.768 kHz crystal on SOSCI/SOSCO; check the board has one")
    return s


def claims_timer(spec):
    return True


def calc_lines(spec: RtccSpec):
    L = ["RTCC setup (p.251-260):",
         "  __builtin_write_OSCCONL(OSCCON | 0x02);   // SOSCEN=1: start the 32.768 kHz secondary oscillator",
         "  __builtin_write_RTCWEN();                 // RCFGCAL.RTCWREN=1 (NVMKEY 0x55/0xAA unlock)",
         "  RCFGCALbits.RTCEN = 0; RCFGCALbits.RTCPTR = 3;  // point at YEAR",
         "  RTCVAL = 0x00YY; RTCVAL = 0xMMDD; RTCVAL = 0x0WHH; RTCVAL = 0xMMSS;  // BCD, pointer auto-decrements",
         "  RCFGCALbits.RTCEN = 1; RCFGCALbits.RTCWREN = 0;"]
    if spec.alarm:
        L.append(f"  alarm: ALCFGRPTbits.AMASK = {AMASK[spec.alarm]:04b} ({spec.alarm}), CHIME = 1, ALRMEN = 1; IEC3bits.RTCIE = 1, "
                 "_RTCCInterrupt clears IFS3bits.RTCIF")
    L += [f"  note: {x}" for x in spec.notes]
    return L, {"spec": spec.to_dict()}


def check(code: str, spec: RtccSpec, rep, emu) -> None:
    g = emu.get
    if g("RCFGCAL", "RTCEN") != 1:
        rep.add("error", "RTCC_EN", "RCFGCALbits.RTCEN is not set: the RTCC does not run (p.253).", 1, 0)
    unlock = re.search(r"__builtin_write_RTCWEN|NVMKEY\s*=\s*0x55", code)
    if not unlock:
        rep.add("error", "RTCC_WREN", "RTCEN/RTCVAL writes need RTCWREN=1, which is only settable after the unlock: "
                "call __builtin_write_RTCWEN() first (p.253).")
    if g("OSCCON", "SOSCEN") != 1 and not re.search(r"OSCCON\s*\|\s*0x0?2\b|SOSCEN\s*=\s*1", code):
        rep.add("error", "RTCC_SOSC", "OSCCON.SOSCEN is not set: the RTCC clock (32.768 kHz SOSC) is off. "
                "Use __builtin_write_OSCCONL(OSCCON | 0x02).", 1, 0)
    if unlock and g("RCFGCAL", "RTCWREN") == 1:
        rep.add("info", "RTCC_LOCK", "RTCWREN is left at 1; clear it after setting the time to protect the clock.")
    if spec.alarm:
        if g("ALCFGRPT", "ALRMEN") != 1:
            rep.add("error", "RTCC_ALARM", "ALCFGRPTbits.ALRMEN is not set: the alarm never fires (p.255).", 1, 0)
        if g("ALCFGRPT", "AMASK") != AMASK[spec.alarm]:
            rep.add("error", "RTCC_AMASK", f"ALCFGRPTbits.AMASK should be {AMASK[spec.alarm]:04b} for an alarm every {spec.alarm}.",
                    AMASK[spec.alarm], g("ALCFGRPT", "AMASK"))
        if g("ALCFGRPT", "CHIME") != 1 and g("ALCFGRPT", "ARPT") == 0:
            rep.add("warning", "RTCC_CHIME", "CHIME=0 and ARPT=0: the alarm fires once only; set CHIME=1 to repeat.")
        from .irq import vector, check_vector
        check_vector(emu, rep, vector("RTCCInterrupt"))


def prompt_text(spec):
    return ("RTCC facts (DS39897C sec. 20; verified later): RCFGCAL p.253 (RTCEN<15>, RTCWREN<13>, RTCSYNC<12>, RTCPTR<9:8>, CAL<7:0>); "
            "ALCFGRPT p.255 (ALRMEN<15>, CHIME<14>, AMASK<13:10>, ALRMPTR<9:8>, ARPT<7:0>). Values are BCD through RTCVAL/ALRMVAL.\n"
            + "\n".join(calc_lines(spec)[0]))


def pages(spec):
    return [251, 253, 255, 260]


FINDING_PAGES = {"RTCC_EN": [253], "RTCC_WREN": [253], "RTCC_SOSC": [123], "RTCC_LOCK": [253], "RTCC_ALARM": [255],
                 "RTCC_AMASK": [255], "RTCC_CHIME": [255]}
