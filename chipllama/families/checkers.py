"""Static checkers for family projects: every register / bit name the HAL writes must exist in the vendor data
(ATDF register map for AVR, the CMSIS device header for STM32), plus family rules (clock, baud error, pins)."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "families"


@lru_cache(maxsize=8)
def avr_names(mcu: str) -> frozenset:
    d = json.loads((DATA / "avr" / f"{mcu}.json").read_text())
    out = set()
    base: dict = {}
    for name, r in sorted(d["registers"].items(), key=lambda kv: kv[1]["addr"]):
        out.add(name)
        m = re.fullmatch(r"(PORT|DDR|PIN)([A-L])", name)
        if m:   # ATDF has no bitfields for port registers; avr-libc names them PORTB5 / DDB5 / PINB5
            px = {"PORT": "PORT", "DDR": "DD", "PIN": "PIN"}[m.group(1)] + m.group(2)
            out.update(f"{px}{i}" for i in range(8))
            out.update(f"{name}{i}" for i in range(8))
        for b, mask in (r.get("bits") or {}).items():
            out.add(b)
            n = bin(int(mask)).count("1")
            if n > 1 or b in base:                     # ATDF field CS0 (mask 7) = avr-libc CS00..CS02; WGM1 split over TCCR1A/B
                k0 = base.get(b, 0)
                out.update(f"{b}{i}" for i in range(k0, k0 + n))
                base[b] = k0 + n
            else:
                base[b] = n
    return frozenset(x for x in out if x)


@lru_cache(maxsize=8)
def cmsis_names(header: str) -> frozenset:
    txt = (DATA / "stm32" / "cmsis" / header).read_text(errors="replace")
    return frozenset(re.findall(r"#define\s+(\w+)", txt))


def _f(sev, code, msg):
    return {"severity": sev, "code": code, "message": msg}


def check_project(p) -> dict:
    code = p.amalg
    fs = []
    regs = {}
    if p.family == "avr" and not p.ard or (p.ard and p.family == "avr" and "hal/hal_pwm.c" in p.files):
        names = avr_names(p.b["mcu"])
        body = "\n".join(t for k, t in p.files.items() if k.startswith("hal/") and k.endswith(".c"))
        used = set(re.findall(r"\b(?:[A-Z]{2,}[A-Z0-9]*\d?[A-Z]?\d?)\b", body))
        cands = {u for u in used if re.fullmatch(r"(TCCR|OCR|ICR|TIMSK|UCSR|UBRR|UDR|TWBR|TWCR|TWSR|TWDR|SPCR|SPSR|SPDR|ADMUX|ADCSR|DDR|PORT|PIN)\w*", u)
                 or re.fullmatch(r"(WGM|CS|COM|OCIE|U2X|UCSZ|RXEN|TXEN|UDRE|RXC|TWINT|TWSTA|TWSTO|TWEN|TWEA|SPE|MSTR|SPR|CPHA|SPIF|REFS|ADEN|ADSC|ADPS|MUX|DDB|PORTB)\d*\w*", u)}
        cands -= {"PORT", "PIN", "DDR"}
        cands = {c for c in cands if not c.startswith(("HAL_", "PORTB_")) and not c.endswith("_")}
        miss = sorted(c for c in cands if c not in names and c not in ("ADC",))
        for c in miss:
            fs.append(_f("error", "AVR_UNKNOWN_REG", f"{c} is not a register/bit of {p.b['mcu']} (ATDF)"))
        fs.append(_f("info", "AVR_REGS", f"{len(cands)} register/bit names checked against the {p.b['mcu']} ATDF"))
        m = re.search(r"HAL_T0_OCR\s+\((\d+)U\)", code)
        if m and not p.ard:
            hz = p.fcy / 64 / (int(m.group(1)) + 1)
            regs["tick_hz"] = hz
            fs.append(_f("info" if abs(hz - 1000) < 1 else "error", "AVR_TICK", f"Timer0 tick {hz:g} Hz"))
    if p.family == "stm32":
        names = cmsis_names(p.sd["header"])
        core = cmsis_names({"f1": "core_cm3.h", "f4": "core_cm4.h", "f0": "core_cm0.h"}[p.series])
        body = "\n".join(t for k, t in p.files.items() if k.startswith("hal/") and k.endswith(".c"))
        used = set(re.findall(r"\b((?:RCC|USART|I2C|SPI|ADC|TIM|FLASH|GPIO|AFIO)_[A-Z0-9_]+)\b", body))
        miss = sorted(u for u in used if u not in names and u not in core and not u.endswith("_BASE") or
                      (u.endswith("_BASE") and u not in names))
        for u in miss:
            fs.append(_f("error", "STM_UNKNOWN_MACRO", f"{u} is not defined in {p.sd['header']} (CMSIS)"))
        inst = set(re.findall(r"\b(USART\d|I2C\d|SPI\d|TIM\d+|ADC\d)->", body))
        for i in sorted(inst):
            if i not in names:
                fs.append(_f("error", "STM_NO_INSTANCE", f"{i} does not exist on {p.b['mcu']}"))
        fs.append(_f("info", "STM_MACROS", f"{len(used)} CMSIS macros and {len(inst)} peripheral instances checked against {p.sd['header']}"))
        for n, u in p.uarts.items():
            m = re.search(rf"HAL_USART{n}_BRR\s+\((\d+)U\)", code)
            if m:
                from .stm32 import _clk
                b = _clk(p, f"USART{n}") / int(m.group(1))
                err = (b / u["baud"] - 1) * 100
                regs[f"USART{n}_baud"] = round(b)
                fs.append(_f("error" if abs(err) > 2.5 else "info", "STM_BAUD", f"USART{n}: {b:.0f} baud ({err:+.2f} %)"))
        for pw in p.pwms:
            m = re.fullmatch(r"(TIM\d+)_CH\d", pw["fn"])
            if m and m.group(1) not in names:
                fs.append(_f("error", "STM_NO_TIMER", f"{m.group(1)} not on {p.b['mcu']}"))
    if p.family == "esp32":
        for b in p.binds:
            for q in b["pins"].values():
                bp = p.by_port.get(q.get("port_name") or "", {})
                if bp.get("note", "").lower().startswith("flash"):
                    fs.append(_f("error", "ESP_FLASH_PIN", f"{bp['label']} is wired to the SPI flash"))
        fs.append(_f("info", "ESP_PINS", "pins checked against the board pin map (flash / input-only / strapping in ERC)"))
    ok = not any(f["severity"] == "error" for f in fs)
    scope = {"avr": "every register/bit name against the ATDF + Timer0 tick",
             "stm32": "every CMSIS macro / peripheral instance against the device header + USART baud error",
             "esp32": "board pin map rules (ERC covers GPIO matrix, strapping, input-only, ADC2/WiFi)"}[p.family]
    return {"verified": ok, "scope": scope, "findings": fs, "registers": regs}
