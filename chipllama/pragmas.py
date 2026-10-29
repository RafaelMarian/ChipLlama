"""Device-generic `#pragma config` generation and validation driven by EDC data."""
from __future__ import annotations

import re

from .board import parse_pragmas, legacy_config_macros
from .edc import Device

# Preferred values (first one the device actually offers wins). Bare-board safe defaults:
# watchdog off, internal oscillator, no crystal, debug/code-protect/write-protect off, JTAG off.
PREFER: dict[str, list[str]] = {
    # watchdog
    "FWDTEN": ["OFF", "ON_SW", "SWDTDIS"], "WDTEN": ["OFF", "SWDTDIS", "SWON"], "WDTE": ["OFF"], "FWDTEN_": ["OFF"],
    "WDTSEN": ["OFF"],
    # oscillator: internal
    "FNOSC": ["FRC", "FRCDIV", "FRCDIVN"], "FOSC": ["INTIO67", "INTOSCIO", "INTRC_NOCLKOUT", "INTOSC", "INTIO2", "INTIO1"],
    "RSTOSC": ["HFINTOSC_1MHZ", "HFINTOSC_64MHZ", "HFINT1", "HFINT32"], "FEXTOSC": ["OFF"],
    "POSCMOD": ["NONE", "OFF"], "POSCMD": ["NONE", "OFF"], "FSOSCEN": ["OFF"], "SOSCEN": ["OFF"],
    "IESO": ["OFF"], "FCMEN": ["OFF"], "PLLCFG": ["OFF"], "OSCIOFNC": ["ON"], "PRICLKEN": ["OFF"],
    # debug / protection
    "DEBUG": ["OFF"], "JTAGEN": ["OFF"], "BKBUG": ["OFF"], "COE": ["OFF"], "XINST": ["OFF"],
    "GCP": ["OFF"], "GWRP": ["OFF"], "GSS": ["DISABLED", "OFF"], "BSS": ["DISABLED", "OFF"], "CSS": ["DISABLED"],
    "BWRP": ["OFF"], "CWRP": ["OFF"], "CP": ["OFF"], "CPD": ["OFF"], "PWP": ["OFF"], "WRT": ["OFF"],
    "DMTDIS": ["OFF"], "PBADEN": ["OFF"],
}
_PROTECT = re.compile(r"^(CP\d*|CPB|CPD|CPC|WRT\w*|EBTR\w*|EBTRB)$")


def _choose(dev: Device, s, ics: str) -> str | None:
    allowed = s.allowed()
    if s.name == "ICS" and ics:
        n = ics[-1]
        for v in allowed:
            if v.endswith(n) and v.upper().startswith("PG"):
                return v
    prefs = PREFER.get(s.name, [])
    if _PROTECT.match(s.name):
        prefs = ["OFF"]
    for v in prefs:
        if v in allowed:
            return v
    return s.default


def default_values(dev: Device, ics: str = "PGx2", overrides: dict | None = None) -> dict[str, str]:
    vals: dict[str, str] = {}
    for s in dev.visible_settings():
        v = _choose(dev, s, ics)
        if v is not None:
            vals[s.name] = v
    for k, v in (overrides or {}).items():
        if dev.setting(k) and dev.setting(k).accepts(v):
            vals[k] = v
    return vals


def render(dev: Device, ics: str = "PGx2", overrides: dict | None = None) -> str:
    vals = default_values(dev, ics, overrides)
    out = [f"// {dev.name} configuration bits ({dev.family}, {dev.compiler.upper()}) - generated from "
           + ("Microchip EDC data" if dev.source != "built-in" else "built-in data")]
    word = None
    for s in dev.visible_settings():
        if s.name not in vals:
            continue
        if s.word != word:
            out.append(f"\n// {s.word}")
            word = s.word
        v = vals[s.name]
        desc = next((x.desc for x in s.values if x.name == v), s.desc)
        out.append(f"#pragma config {s.name} = {v}".ljust(38) + (f"// {desc.strip()}" if desc else ""))
    return "\n".join(out).replace("\n\n", "\n", 1)


# ---------------------------------------------------------------- validation
_BITS = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*?)bits\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)")
CRITICAL = ("FNOSC", "FOSC", "RSTOSC", "FWDTEN", "WDTEN", "WDTE", "ICS")


def check(code: str, dev: Device, add, ics: str = "PGx2", require: bool = True) -> dict[str, str]:
    """Validate every `#pragma config` against the device; add(severity, code, msg, expected, actual)."""
    vals, probs = parse_pragmas(code)
    for p in probs:
        add("error", "CONFIG_SYNTAX", p)
    if legacy_config_macros(code):
        add("warning", "CONFIG_LEGACY", "_CONFIGx() macros found; use `#pragma config NAME = VALUE` lines.")
    for k, v in vals.items():
        s = dev.setting(k)
        if s is None:
            add("error", "CONFIG_UNKNOWN", f"'{k}' is not a configuration setting of {dev.name}.", None, k)
            continue
        if not s.accepts(v):
            opts = ", ".join(s.allowed()) or (f"{s.range[0]:#x}..{s.range[1]:#x}" if s.range else "?")
            add("error", "CONFIG_VALUE", f"{k} = {v} is not valid for {dev.name} (allowed: {opts}).", opts, v)
    if require:
        want = default_values(dev, ics)
        missing = [k for k in want if k not in vals]
        crit = [k for k in missing if k in CRITICAL]
        if crit:
            add("error", "CONFIG_MISSING", "critical configuration bits missing: " + ", ".join(crit)
                + " (a bare board has nothing else to set them).", ", ".join(f"{k}={want[k]}" for k in crit), "absent")
        rest = [k for k in missing if k not in CRITICAL]
        if rest:
            add("warning", "CONFIG_INCOMPLETE", "configuration settings left at erased defaults: " + ", ".join(rest[:20])
                + (" ..." if len(rest) > 20 else ""), None, "absent")
        if "ICS" in vals and dev.setting("ICS") and vals["ICS"] != want.get("ICS") and want.get("ICS"):
            add("error", "ICS", f"ICS = {vals['ICS']} but the programmer is wired to pair {ics[-1]} ({want['ICS']}).",
                want["ICS"], vals["ICS"])
        for k in ("FWDTEN", "WDTEN", "WDTE"):
            if k in vals and vals[k] in ("ON", "SWON", "NOSLP") and want.get(k) != vals[k]:
                add("warning", "WDT", f"{k} = {vals[k]} enables the watchdog: the code must clear it periodically.")
    return vals


def check_sfr_fields(code: str, dev: Device, add) -> None:
    """Flag `REGbits.FIELD` uses whose register or bitfield does not exist on this device."""
    from .cemu import strip_comments
    bad, unknown = [], []
    for reg, fld in dict.fromkeys(_BITS.findall(strip_comments(code))):
        r = dev.sfr(reg)
        if r is None:
            unknown.append(reg)
            continue
        names = r.field_names()
        if not names:
            continue
        if fld not in names:
            bad.append((reg, fld, r))
    for reg, fld, r in bad:
        sugg = ", ".join(sorted(f.name for f in r.fields)[:12])
        add("error", "SFR_FIELD", f"{reg}bits.{fld} does not exist on {dev.name} (fields: {sugg}).", sugg, fld)
    if unknown:
        add("error", "SFR_UNKNOWN", f"registers not present on {dev.name}: " + ", ".join(sorted(set(unknown))[:10]))
