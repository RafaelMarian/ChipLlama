"""Registry of the deterministic peripheral modules (GPIO, interrupts, ADC, OC/PWM, IC).

Each module exposes:
    parse_request(text, fcy) -> Spec | None   intent parsing (None = request is not about it)
    calc_lines(spec) -> (lines, dict)           calculator output
    check(code, spec, rep, emu)                 checker rules (rep.add(sev, code, msg, expected, actual))
    prompt_text(spec) -> str                    register facts injected into the first prompt
    pages(spec) -> list[int]                    datasheet pages to attach as images
"""
from __future__ import annotations

import importlib

MODULES = {"gpio": "gpio", "irq": "interrupts", "adc": "adc", "pwm": "pwm", "ic": "ic",
           "spi": "spi", "i2c": "i2c", "rtcc": "rtcc", "crc": "crc", "pmp": "pmp", "power": "power", "usb": "usb"}        # kind -> module name


def _mod(kind: str):
    return importlib.import_module(f".{MODULES[kind]}", __package__)


_BUS = r"(spi|sck|sclk|i2c|scl|sda|bus|baud|pwm|oc\d|clock\s+of\s+the\s+(?:spi|i2c))"


def fcy_from_text(text: str, fosc_hz: float | None = None, default: float | None = 16e6) -> float | None:
    """Fcy from an explicit CPU 'NN MHz' clock in the text (Fosc unless it says Fcy/MIPS), else `default`.
    MHz values that belong to a bus (SPI SCK, I2C 1 MHz, PWM) are ignored."""
    import re
    if fosc_hz:
        return fosc_hz / 2
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*MHz(\s*(?:fcy|instruction|mips))?", text, re.I):
        before = text[max(0, m.start() - 25):m.start()]
        after = text[m.end():m.end() + 15]
        if re.search(_BUS, before + " " + after, re.I) and not re.search(r"fosc|cpu|crystal|oscillator|system|fcy|mips",
                                                                           before + after, re.I):
            continue
        f = float(m.group(1)) * 1e6
        return f if m.group(2) else f / 2
    m = re.search(r"(\d+(?:\.\d+)?)\s*MIPS", text, re.I)
    return float(m.group(1)) * 1e6 if m else default


def calc(kind: str, text: str, fosc_hz: float | None = None):
    if kind not in MODULES:
        raise ValueError(f"unknown calculator '{kind}' (have: timer, uart, {', '.join(MODULES)})")
    m = _mod(kind)
    spec = m.parse_request(text, fcy_from_text(text, fosc_hz), force=True)
    return m.calc_lines(spec)


def parse_all(text: str, fcy: float) -> dict:
    out = {}
    for kind in MODULES:
        sp = _mod(kind).parse_request(text, fcy)
        if sp is not None:
            out[kind] = sp
    return out


def check_all(code: str, specs: dict, rep, emu, *, audit: bool = True) -> None:
    for kind, sp in specs.items():
        _mod(kind).check(code, sp, rep, emu)
    if audit:
        from .interrupts import audit as _audit
        _audit(code, rep, emu)


def prompt_all(specs: dict) -> str:
    return "\n\n".join(_mod(k).prompt_text(sp) for k, sp in specs.items())


def pages_all(specs: dict) -> list[int]:
    out: list[int] = []
    for k, sp in specs.items():
        out += _mod(k).pages(sp)
    return list(dict.fromkeys(out))


def finding_pages() -> dict:
    out = {}
    for k in MODULES:
        out.update(getattr(_mod(k), "FINDING_PAGES", {}))
    return out


def claims_timer(specs: dict) -> bool:
    """True when a peripheral spec (PWM/IC/ADC) explains the timer words itself."""
    return any(getattr(_mod(k), "claims_timer", lambda s: False)(sp) for k, sp in specs.items())


def timer_spec(specs: dict):
    """A TimerSpec some peripheral needs (e.g. ADC timer trigger), or None."""
    for k, sp in specs.items():
        f = getattr(_mod(k), "timer_spec", None)
        ts = f(sp) if f else None
        if ts is not None:
            return ts
    return None


def allowed_config(specs: dict) -> set:
    """Board-fixed config settings a request legitimately turns on (e.g. FWDTEN for a watchdog request)."""
    out: set = set()
    for k, sp in specs.items():
        f = getattr(_mod(k), "allow_config", None)
        if f:
            out |= f(sp)
    return out


def check_full(code: str, specs: dict, *, fcy: float, profile=None, ics: str = "PGx2", rep=None, require_config=True):
    """Peripheral-only request (no timer/UART spec): peripheral rules + configuration bits / clock tree."""
    from .checker import CheckReport
    from .cemu import emulate
    from . import board as boardmod
    rep = rep or CheckReport()
    emu = emulate(code)
    for w in emu.warnings:
        rep.add("warning", "UNPARSED", w)
    check_all(code, specs, rep, emu)
    if require_config:
        prof = profile or boardmod.get_profile("frcpll")
        boardmod.check_config(code, fcy * 2, emu.get("CLKDIV", "RCDIV"), emu.get("CLKDIV", "CPDIV"), prof, ics, rep.add,
                              allow=allowed_config(specs))
    return rep
