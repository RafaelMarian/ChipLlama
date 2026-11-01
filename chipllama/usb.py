"""USB (DS39897C sec. 18, p.207): recognised, but NOT generated or verified by ChipLlama.
A USB device/host needs the Microchip USB stack (MLA "USB Device/Host" or MCC Melody USB).  ChipLlama only checks the
clock prerequisites: the 96 MHz PLL must be enabled (PLL_96MHZ = ON) with a 4 MHz PLL input (PLLDIV matched to the
source) so that the USB module gets 48 MHz, and USB full-speed needs +-0.25 % clock accuracy (crystal/EC, not FRC)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORDS = re.compile(r"\b(usb|cdc|hid device|otg)\b", re.I)
LABEL = "UNSUPPORTED-for-verification"


@dataclass
class UsbSpec:
    notes: list[str] = field(default_factory=list)
    fcy: float = 16e6

    def to_dict(self):
        return {"status": LABEL}


def parse_request(text, fcy=16e6, force=False):
    if not force and not _WORDS.search(text):
        return None
    return UsbSpec(fcy=fcy, notes=[f"USB is {LABEL}: use the Microchip USB stack (MLA or MCC Melody); ChipLlama checks only "
                                   "the clock/config prerequisites"])


def calc_lines(spec):
    L = [f"USB: {LABEL}",
         "  ChipLlama does not generate a USB stack. Use Microchip Libraries for Applications (MLA) v2018_11_26",
         "  (apps/usb/device/cdc_basic for PIC24FJ256GB110) or MCC Melody USB.",
         "  Clock prerequisites (p.207, p.290):",
         "    #pragma config PLL_96MHZ = ON   // 96 MHz PLL -> /2 = 48 MHz USB clock",
         "    #pragma config PLLDIV = DIV2     // PLL input must be 4 MHz: DIV2 for an 8 MHz crystal, NODIV for 4 MHz/FRC",
         "    #pragma config DISUVREG = OFF    // ON = internal USB 3.3 V regulator enabled when VUSB is supplied",
         "    #pragma config POSCMOD = HS/XT   // USB full-speed needs +-0.25%: FRC (+-2%) is not accurate enough",
         "  VBUS/VUSB/D+/D- (RG2/RG3) wiring must follow the datasheet USB section."]
    return L, {"status": LABEL}


def check(code, spec, rep, emu):
    from .board import parse_pragmas
    v, _ = parse_pragmas(code)
    rep.add("warning", "USB_UNSUPPORTED", f"USB code is {LABEL}: only the clock prerequisites are checked.")
    if v.get("PLL_96MHZ") != "ON":
        rep.add("error", "USB_PLL", "PLL_96MHZ must be ON: the USB module needs the 96 MHz PLL / 2 = 48 MHz (p.207).", "ON", v.get("PLL_96MHZ"))
    if v.get("FNOSC") in ("FRC", "FRCPLL", "FRCDIV", "LPRC") or v.get("POSCMOD", "NONE") == "NONE":
        rep.add("warning", "USB_CLOCK", "USB full-speed needs a +-0.25% clock; FRC is +-2% - use a crystal (POSCMOD = HS/XT).")


def prompt_text(spec):
    return "USB: " + LABEL + ". Explain that the Microchip USB stack (MLA/MCC) is required.\n" + "\n".join(calc_lines(spec)[0])


def pages(spec):
    return [207, 290]


FINDING_PAGES = {"USB_UNSUPPORTED": [207], "USB_PLL": [207, 290], "USB_CLOCK": [207]}
