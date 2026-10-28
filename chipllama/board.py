"""Board / clock profiles and configuration-word (#pragma config) handling for PIC24FJ256GB110.

Bit meanings: DS39897C Section 26 (CW1 p.288, CW2 p.290, CW3 p.291), oscillator Section 8
(Table 8-1, CLKDIV Register 8-2 p.125, 8.5.1 p.129).
XC16 setting NAMES/VALUES are not in the datasheet; they match the MPLAB X "Configuration Bits"
output / p24FJ256GB110.h listings for this exact device (see README "Configuration bits").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FRC_HZ = 8_000_000
LPRC_HZ = 31_000
SOSC_HZ = 32_768
RCDIV_DIV = [1, 2, 4, 8, 16, 32, 64, 256]
CPDIV_HZ = [32_000_000, 16_000_000, 8_000_000, 4_000_000]
PLLDIV_N = {"NODIV": 1, "DIV2": 2, "DIV3": 3, "DIV4": 4, "DIV5": 5, "DIV6": 6, "DIV10": 10, "DIV12": 12}
ICS_PAIRS = ("PGx1", "PGx2", "PGx3")

# setting -> (config word, allowed values or regex)
SETTINGS: dict[str, tuple[str, object]] = {
    "WDTPS": ("CONFIG1", re.compile(r"PS(1|2|4|8|16|32|64|128|256|512|1024|2048|4096|8192|16384|32768)$")),
    "FWPSA": ("CONFIG1", {"PR32", "PR128"}),
    "WINDIS": ("CONFIG1", {"OFF", "ON"}),
    "FWDTEN": ("CONFIG1", {"OFF", "ON"}),
    "ICS": ("CONFIG1", set(ICS_PAIRS)),
    "GWRP": ("CONFIG1", {"OFF", "ON"}),
    "GCP": ("CONFIG1", {"OFF", "ON"}),
    "JTAGEN": ("CONFIG1", {"OFF", "ON"}),
    "POSCMOD": ("CONFIG2", {"EC", "XT", "HS", "NONE"}),
    "DISUVREG": ("CONFIG2", {"OFF", "ON"}),
    "IOL1WAY": ("CONFIG2", {"OFF", "ON"}),
    "OSCIOFNC": ("CONFIG2", {"OFF", "ON"}),
    "FCKSM": ("CONFIG2", {"CSECME", "CSECMD", "CSDCMD"}),
    "FNOSC": ("CONFIG2", {"FRC", "FRCPLL", "PRI", "PRIPLL", "SOSC", "LPRC", "FRCDIV"}),
    "PLL_96MHZ": ("CONFIG2", {"ON", "OFF"}),
    "PLLDIV": ("CONFIG2", set(PLLDIV_N)),
    "IESO": ("CONFIG2", {"OFF", "ON"}),
    "WPFP": ("CONFIG3", re.compile(r"WPFP\d{1,3}$")),
    "WPDIS": ("CONFIG3", {"WPDIS", "WPEN"}),
    "WPCFG": ("CONFIG3", {"WPCFGDIS", "WPCFGEN"}),
    "WPEND": ("CONFIG3", {"WPENDMEM", "WPSTARTMEM"}),
}
COMMENTS = {
    "WPFP": "Write-protect boundary (unused, WPDIS)", "WPDIS": "Segmented code protection disabled",
    "WPCFG": "Config page not protected", "WPEND": "(only with WPDIS=WPEN)",
    "POSCMOD": "Primary oscillator DISABLED (no crystal on the board)",
    "DISUVREG": "Internal USB 3.3V regulator disabled", "IOL1WAY": "PPS registers can be rewritten",
    "OSCIOFNC": "OSCO/RC15 pin is general I/O (not CLKO)", "FCKSM": "Clock switching and fail-safe monitor disabled",
    "FNOSC": "", "PLL_96MHZ": "96 MHz PLL enabled", "PLLDIV": "", "IESO": "Two-speed start-up disabled",
    "WDTPS": "WDT postscaler (WDT off)", "FWPSA": "WDT prescaler (WDT off)", "WINDIS": "Standard WDT",
    "FWDTEN": "Watchdog timer disabled", "ICS": "", "GWRP": "No program-memory write protect",
    "GCP": "No code protection", "JTAGEN": "JTAG disabled (frees RA0/RA1/RA4/RA5)",
}


@dataclass
class BoardProfile:
    key: str
    title: str
    fosc_hz: float
    clock: dict[str, str]           # FNOSC/POSCMOD/PLL_96MHZ/PLLDIV
    clock_code: list[str] = field(default_factory=list)   # statements main() must run first
    crystal_hz: float = 0.0          # 0 = no crystal on the board
    note: str = ""


def _profiles(crystal_hz: float = 8_000_000) -> dict[str, BoardProfile]:
    pll_div = {4e6: "NODIV", 8e6: "DIV2", 12e6: "DIV3", 16e6: "DIV4", 20e6: "DIV5", 24e6: "DIV6",
               40e6: "DIV10", 48e6: "DIV12"}.get(float(crystal_hz), "DIV2")
    return {
        "frcpll": BoardProfile(
            "frcpll", "Internal FRC 8 MHz + 96 MHz PLL (Fosc 32 MHz, Fcy 16 MHz) - bare board, no crystal",
            32e6, {"POSCMOD": "NONE", "FNOSC": "FRCPLL", "PLL_96MHZ": "ON", "PLLDIV": "NODIV"},
            ["CLKDIVbits.RCDIV = 1;   /* FRC/2 = 4 MHz into the PLL (POR value) */",
             "CLKDIVbits.CPDIV = 0;   /* 96 MHz/3 = 32 MHz -> Fosc 32 MHz */"],
            note="RCDIV resets to 001 (FRC/2 = 4 MHz), so PLLDIV = NODIV feeds the PLL its required 4 MHz from reset."),
        "frcpll_div2": BoardProfile(
            "frcpll_div2", "Internal FRC 8 MHz undivided + PLLDIV/2 (Fosc 32 MHz) - needs RCDIV=0 in code",
            32e6, {"POSCMOD": "NONE", "FNOSC": "FRCPLL", "PLL_96MHZ": "ON", "PLLDIV": "DIV2"},
            ["CLKDIVbits.RCDIV = 0;   /* FRC 8 MHz undivided, PLLDIV/2 -> 4 MHz */",
             "CLKDIVbits.CPDIV = 0;   /* Fosc 32 MHz */"],
            note="Until RCDIV is written the PLL sees 2 MHz (out of spec); prefer 'frcpll'."),
        "frc": BoardProfile(
            "frc", "Internal FRC 8 MHz, no PLL (Fosc 8 MHz, Fcy 4 MHz)",
            8e6, {"POSCMOD": "NONE", "FNOSC": "FRC", "PLL_96MHZ": "OFF", "PLLDIV": "NODIV"}),
        "xtal": BoardProfile(
            "xtal", f"External {crystal_hz/1e6:g} MHz crystal (HS) + 96 MHz PLL (Fosc 32 MHz)",
            32e6, {"POSCMOD": "HS" if crystal_hz > 10e6 else "XT", "FNOSC": "PRIPLL", "PLL_96MHZ": "ON",
                   "PLLDIV": pll_div}, ["CLKDIVbits.CPDIV = 0;   /* Fosc 32 MHz */"], crystal_hz=float(crystal_hz)),
    }


PROFILE_KEYS = ("frcpll", "frcpll_div2", "frc", "xtal")


def get_profile(key: str = "frcpll", crystal_hz: float = 8_000_000) -> BoardProfile:
    p = _profiles(crystal_hz)
    if key not in p:
        raise ValueError(f"unknown clock profile {key!r}; choose one of {', '.join(PROFILE_KEYS)}")
    return p[key]


def pragma_values(profile: BoardProfile, ics: str = "PGx2") -> dict[str, str]:
    if ics not in ICS_PAIRS:
        raise ValueError(f"ICS must be one of {ICS_PAIRS}")
    v = {"WPFP": "WPFP511", "WPDIS": "WPDIS", "WPCFG": "WPCFGDIS", "WPEND": "WPENDMEM",
         "POSCMOD": "NONE", "DISUVREG": "OFF", "IOL1WAY": "OFF", "OSCIOFNC": "ON", "FCKSM": "CSDCMD",
         "FNOSC": "FRCPLL", "PLL_96MHZ": "ON", "PLLDIV": "NODIV", "IESO": "OFF",
         "WDTPS": "PS32768", "FWPSA": "PR128", "WINDIS": "OFF", "FWDTEN": "OFF", "ICS": ics,
         "GWRP": "OFF", "GCP": "OFF", "JTAGEN": "OFF"}
    v.update(profile.clock)
    if profile.crystal_hz:
        v["OSCIOFNC"] = "OFF"
    return v


def render_pragmas(profile: BoardProfile, ics: str = "PGx2") -> str:
    vals = pragma_values(profile, ics)
    out, word = [], None
    n = ics[-1]
    for k, val in vals.items():
        w = SETTINGS[k][0]
        if w != word:
            out.append(f"// {w}")
            word = w
        c = COMMENTS.get(k, "")
        if k == "ICS":
            c = f"Debug/programming on PGEC{n}/PGED{n} (must match YOUR board wiring)"
        elif k == "FNOSC":
            c = {"FRCPLL": "FRC + PLL", "FRC": "FRC 8 MHz", "PRIPLL": "Primary osc + PLL"}.get(val, val)
        elif k == "PLLDIV":
            c = "PLL prescaler -> PLL input must be 4 MHz"
        elif k == "POSCMOD" and val != "NONE":
            c = f"Primary oscillator {val}"
        out.append(f"#pragma config {k} = {val}".ljust(34) + (f"// {c}" if c else ""))
    return "\n".join(out)


_PRAGMA = re.compile(r"^[ \t]*#[ \t]*pragma[ \t]+config[ \t]+(.+)$", re.M)


def parse_pragmas(code: str) -> tuple[dict[str, str], list[str]]:
    """Return ({SETTING: VALUE}, problems). Handles `#pragma config A = B, C = D`."""
    vals: dict[str, str] = {}
    probs: list[str] = []
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    for m in _PRAGMA.finditer(code):
        body = m.group(1).split("//")[0]
        for part in body.split(","):
            if not part.strip():
                continue
            if "=" not in part:
                probs.append(f"malformed '#pragma config {part.strip()}'")
                continue
            k, v = (x.strip() for x in part.split("=", 1))
            if k in vals and vals[k] != v:
                probs.append(f"{k} set twice ({vals[k]} and {v})")
            vals[k] = v
    return vals, probs


def legacy_config_macros(code: str) -> bool:
    return bool(re.search(r"^\s*_CONFIG[123]\s*\(", code, re.M))


@dataclass
class ClockResult:
    fosc_hz: float | None
    errors: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def derive_fosc(vals: dict[str, str], rcdiv: int, cpdiv: int, crystal_hz: float = 0.0) -> ClockResult:
    r = ClockResult(None)
    fnosc, poscmod = vals.get("FNOSC"), vals.get("POSCMOD")
    if fnosc in ("PRI", "PRIPLL") or (poscmod and poscmod != "NONE"):
        if not crystal_hz:
            r.errors.append(("NO_CRYSTAL", f"FNOSC={fnosc}/POSCMOD={poscmod} needs a primary crystal/oscillator but the "
                                           "board profile has none: use FNOSC=FRCPLL (or FRC) with POSCMOD=NONE."))
            return r
    if fnosc == "FRC":
        r.fosc_hz = FRC_HZ
    elif fnosc == "FRCDIV":
        r.fosc_hz = FRC_HZ / RCDIV_DIV[rcdiv]
    elif fnosc == "LPRC":
        r.fosc_hz = LPRC_HZ
    elif fnosc == "SOSC":
        r.errors.append(("NO_CRYSTAL", "FNOSC=SOSC needs a 32.768 kHz crystal on SOSCI/SOSCO."))
    elif fnosc == "PRI":
        r.fosc_hz = crystal_hz
    elif fnosc in ("FRCPLL", "PRIPLL"):
        src = FRC_HZ / RCDIV_DIV[rcdiv] if fnosc == "FRCPLL" else crystal_hz
        if fnosc == "FRCPLL" and rcdiv not in (0, 1):
            r.errors.append(("PLL_INPUT", f"FRCPLL needs RCDIV=000 (8 MHz) or 001 (4 MHz) (DS39897C 8.5.1); code leaves RCDIV={rcdiv}."))
            return r
        if vals.get("PLL_96MHZ") != "ON":
            r.errors.append(("PLL_OFF", f"FNOSC={fnosc} but PLL_96MHZ is not ON: the 96 MHz PLL would be disabled."))
            return r
        n = PLLDIV_N.get(vals.get("PLLDIV", ""), 0)
        if not n:
            r.errors.append(("PLL_INPUT", "PLLDIV missing/invalid for a PLL clock mode."))
            return r
        pin = src / n
        if abs(pin - 4e6) > 1:
            fix = ("PLLDIV = NODIV with RCDIV=1 (reset value), or PLLDIV = DIV2 with CLKDIVbits.RCDIV = 0"
                   if fnosc == "FRCPLL" else f"PLLDIV that divides {src/1e6:g} MHz down to 4 MHz")
            r.errors.append(("PLL_INPUT", f"PLL input = {src/1e6:g} MHz / {n} = {pin/1e6:g} MHz but the 96 MHz PLL needs exactly 4 MHz. Use {fix}."))
            return r
        r.fosc_hz = CPDIV_HZ[cpdiv]
        r.notes.append(f"{fnosc}: {src/1e6:g} MHz /{n} = 4 MHz -> 96 MHz PLL /3 = 32 MHz -> CPDIV={cpdiv} -> Fosc {r.fosc_hz/1e6:g} MHz")
    else:
        r.errors.append(("CONFIG_MISSING", "FNOSC is not set."))
    return r


REQUIRED = tuple(SETTINGS)
BOARD_FIXED = ("FWDTEN", "JTAGEN", "GCP", "GWRP")  # must equal OFF


def check_config(code: str, spec_fosc_hz: float, rcdiv: int, cpdiv: int, profile: BoardProfile,
                 ics: str, add) -> float | None:
    """Append findings through add(sev, code, msg, expected, actual). Returns the derived Fosc."""
    vals, probs = parse_pragmas(code)
    for p in probs:
        add("error", "CONFIG_SYNTAX", p)
    if legacy_config_macros(code):
        add("warning", "CONFIG_LEGACY", "_CONFIGx() macros found; use `#pragma config NAME = VALUE` lines instead.")
    expected = pragma_values(profile, ics)
    missing = [k for k in REQUIRED if k not in vals]
    if missing:
        add("error", "CONFIG_MISSING", "configuration bits missing - emit `#pragma config` for: " + ", ".join(missing)
            + ". The board has nothing else to set them.", ", ".join(missing), "absent")
    for k, v in vals.items():
        if k not in SETTINGS:
            add("error", "CONFIG_UNKNOWN", f"'{k}' is not a PIC24FJ256GB110 configuration setting.")
            continue
        allowed = SETTINGS[k][1]
        ok = bool(allowed.match(v)) if hasattr(allowed, "match") else v in allowed
        if not ok:
            add("error", "CONFIG_VALUE", f"{k} = {v} is not a valid value.", None, v)
    if vals.get("ICS") and vals["ICS"] != ics:
        add("error", "ICS", f"ICS = {vals['ICS']} but the board's PICkit is wired to PGEC{ics[-1]}/PGED{ics[-1]}: "
                            "debugging would fail.", ics, vals["ICS"])
    for k in BOARD_FIXED:
        if vals.get(k, "OFF") != "OFF":
            add("error", k, f"{k} must be OFF on this board.", "OFF", vals[k])
    if not profile.crystal_hz and vals.get("POSCMOD", "NONE") != "NONE":
        add("error", "POSCMOD", "board has no crystal: POSCMOD must be NONE.", "NONE", vals["POSCMOD"])
    if not profile.crystal_hz and vals.get("OSCIOFNC") == "OFF":
        add("info", "OSCIOFNC", "OSCIOFNC = OFF puts CLKO on RC15; ON makes it a normal I/O pin.")
    if vals.get("IESO") == "ON":
        add("info", "IESO", "IESO=ON (two-speed start-up) is only useful with a primary oscillator.")
    if "FNOSC" not in vals:
        return None
    clk = derive_fosc(vals, rcdiv, cpdiv, profile.crystal_hz)
    for c, m in clk.errors:
        add("error", c, m)
    for n in clk.notes:
        add("info", "CLOCK_PATH", n)
    if clk.fosc_hz is not None and abs(clk.fosc_hz - spec_fosc_hz) > 1:
        add("error", "CLOCK_CONFIG", f"configuration bits + CLKDIV give Fosc = {clk.fosc_hz/1e6:g} MHz but the timing math "
                                     f"assumes Fosc = {spec_fosc_hz/1e6:g} MHz.", f"{spec_fosc_hz/1e6:g} MHz", f"{clk.fosc_hz/1e6:g} MHz")
    if expected.get("FNOSC") != vals.get("FNOSC") and clk.fosc_hz:
        add("info", "PROFILE", f"FNOSC={vals.get('FNOSC')} differs from board profile '{profile.key}' ({expected['FNOSC']}), but is consistent.")
    return clk.fosc_hz
