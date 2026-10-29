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

MODULES = {}        # kind -> module name (filled as peripherals are added)


def _mod(kind: str):
    return importlib.import_module(f".{MODULES[kind]}", __package__)


def fcy_from_text(text: str, fosc_hz: float | None = None) -> float:
    from .timers import parse_request
    if fosc_hz:
        return fosc_hz / 2
    return parse_request(text).fcy


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


def check_all(code: str, specs: dict, rep, emu) -> None:
    for kind, sp in specs.items():
        _mod(kind).check(code, sp, rep, emu)


def prompt_all(specs: dict) -> str:
    return "\n\n".join(_mod(k).prompt_text(sp) for k, sp in specs.items())


def pages_all(specs: dict) -> list[int]:
    out: list[int] = []
    for k, sp in specs.items():
        out += _mod(k).pages(sp)
    return list(dict.fromkeys(out))
