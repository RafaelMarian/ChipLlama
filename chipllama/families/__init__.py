"""MCU family plugins (M8): Arduino AVR, STM32, ESP32 next to the PIC core.

A family plugin bundles
  * board / device data   boards.py      (vendor ATDF / STM32 open pin data / Espressif datasheet tables)
  * electrical limits     LIMITS          (absolute-max and spec'd pin currents with their datasheet sections)
  * pin-function rules    auto_fn / erc   (Circuit editor + Pin planner)
  * HAL templates         project.py      (bare-metal C, Arduino sketch, CMSIS + Makefile)
  * checker               checkers.py     (AVR: ATDF registers/bitfields, STM32: RCC/MODER/AFR/BRR, ESP32: pad rules)
  * toolchain             toolchain.py    (avr-gcc / arduino-cli / arm-none-eabi-gcc, Doctor install hints)
PIC stays the primary family: every PIC code path is untouched when family_of(device) is None.
"""
from __future__ import annotations

import re

from .boards import boards, resolve, ALIASES, stm32_data  # noqa: F401

FAMILIES = {
    "avr": {"title": "Arduino AVR (ATmega328P / ATmega2560)", "status": "full"},
    "stm32": {"title": "STM32 (F103 / F411 / F030)", "status": "full"},
    "esp32": {"title": "ESP32 (Arduino core)", "status": "full"},
    "fpga": {"title": "FPGA (iCE40 / ECP5, Verilog)", "status": "roadmap"},
}

# Electrical limits (per family / MCU) with the datasheet section they come from.
LIMITS = {
    "ATMEGA328P": {"abs": 40.0, "spec": 20.0, "total": 200.0, "spec_txt": "VOH 4.1 V @ 20 mA, VCC 5 V",
                   "ref_abs": "ATmega328P DS40002061 §28.1 (Absolute Maximum Ratings)",
                   "ref_spec": "ATmega328P DS40002061 §28.2 (DC characteristics)"},
    "ATMEGA2560": {"abs": 40.0, "spec": 20.0, "total": 200.0, "spec_txt": "VOH 4.2 V @ 20 mA, VCC 5 V",
                   "ref_abs": "ATmega2560 DS2549 §31.1 (Absolute Maximum Ratings)",
                   "ref_spec": "ATmega2560 DS2549 §31.2 (DC characteristics)"},
    "STM32F103C8": {"abs": 25.0, "spec": 8.0, "total": 150.0, "spec_txt": "VOH VDD-0.4 V @ 8 mA",
                    "ref_abs": "DS5319 Table 7 (IIO, ΣIVDD)", "ref_spec": "DS5319 Table 35 (output voltage)"},
    "STM32F411RE": {"abs": 25.0, "spec": 8.0, "total": 160.0, "spec_txt": "VOH VDD-0.4 V @ 8 mA",
                    "ref_abs": "DS10314 Table 12 (IIO, ΣIVDD)", "ref_spec": "DS10314 Table 55 (output voltage)"},
    "STM32F030F4": {"abs": 25.0, "spec": 8.0, "total": 120.0, "spec_txt": "VOH VDD-0.4 V @ 8 mA",
                    "ref_abs": "DS9773 Table 20 (IIO, ΣIVDD)", "ref_spec": "DS9773 Table 47 (output voltage)"},
    "ESP32": {"abs": 28.0, "spec": 20.0, "total": 1200.0, "spec_txt": "IOL 28 mA / IOH 40 mA max, VOH 0.8*VDD",
              "ref_abs": "ESP32 datasheet §5.4 (DC characteristics, IOL/IOH)", "ref_spec": "ESP32 datasheet §5.4"},
    "ESP32C3": {"abs": 28.0, "spec": 20.0, "total": 1200.0, "spec_txt": "IOL 28 mA / IOH 40 mA max, VOH 0.8*VDD",
                "ref_abs": "ESP32-C3 datasheet §5.5 (DC characteristics)", "ref_spec": "ESP32-C3 datasheet §5.5"},
}

FQBN = {"ARDUINO-UNO": "arduino:avr:uno", "ARDUINO-NANO": "arduino:avr:nano:cpu=atmega328",
        "ARDUINO-MEGA2560": "arduino:avr:mega:cpu=atmega2560", "ESP32-DEVKITC": "esp32:esp32:esp32",
        "ESP32-C3-DEVKITM": "esp32:esp32:esp32c3"}


def family_of(device: str | None) -> str | None:
    bid = resolve(device or "")
    return boards()[bid]["family"] if bid else None


def board(device: str) -> dict | None:
    bid = resolve(device or "")
    return boards()[bid] if bid else None


def is_family(device: str | None) -> bool:
    return family_of(device) is not None


def limits(device: str) -> dict:
    b = board(device)
    return dict(LIMITS[b["mcu"]], vdd=b["vdd"])


def device_list() -> list[dict]:
    return [{"id": k, "title": v["title"], "family": v["family"]} for k, v in boards().items()]


def info(device: str) -> dict:
    """pinout.get()-compatible record for a board."""
    import copy
    b = board(device)
    pins = copy.deepcopy(b["pins"])
    for p in pins:
        p.setdefault("rp", None)
        p.setdefault("rpi", None)
        p.setdefault("cn", None)
    src = {"avr": f"Microchip ATDF {b['mcu']}.atdf + Arduino board schematic",
           "stm32": f"STM32_open_pin_data {b['mcu']} + board user manual",
           "esp32": "Espressif datasheet IO_MUX / GPIO matrix tables + DevKit schematic"}[b["family"]]
    return {"device": b["id"], "family": b["family"], "arch": b["family"], "pps": None, "source": src, "generic": False,
            "verified": False, "count": len(pins), "layout": "board", "pins": pins, "board": True, "title": b["title"],
            "mcu": b["mcu"], "vdd": b["vdd"], "fcpu": b["fcpu"]}


# ---------------------------------------------------------------- Circuit hooks
_PAIR = {"rx": ("U", "TX"), "tx": ("U", "RX"), "scl": ("SCL", ""), "sda": ("SDA", ""),
         "sck": ("SCK", ""), "mosi": ("SDO", ""), "miso": ("SDI", "")}


def _inst_of(fn: str) -> str | None:
    m = re.fullmatch(r"U(\d)(TX|RX)|(?:SCL|SDA|SCK|SDO|SDI)(\d)", fn or "")
    return (m.group(1) or m.group(3)) if m else None


def _fn_for(role: str, inst: str) -> str:
    pre, suf = _PAIR[role]
    return f"U{inst}{suf}" if pre == "U" else f"{pre}{inst}"


def auto_fn(c, pin: str, role: str, b: dict) -> str:
    """Pick a function for a family pin; bus pins of one part share one peripheral instance."""
    fam = c.info["family"]
    p = c.pins.get(pin, {})
    valid = p.get("fns", [])
    if role in ("out", "cs"):
        return "GPIO_OUT"
    if role == "in":
        return "GPIO_IN"
    if role == "io":
        return "GPIO_IO"
    if role == "an":
        if fam == "esp32":
            return next((f for f in valid if f.startswith("ADC1_")), next((f for f in valid if f.startswith("ADC")), "ADC?"))
        return f"AN{p['an']}" if p.get("an") is not None else "AN?"
    if role == "pwm":
        pat = {"avr": r"OC\d[A-C]", "stm32": r"TIM\d+_CH\d", "esp32": r"LEDC"}[fam]
        used = set(getattr(c, "assign", {}).values()) | set(getattr(c, "_fam_used", set()))
        have = [f for f in valid if re.fullmatch(pat, f)]
        f = next((f for f in have if f not in used or fam == "esp32"), have[0] if have else "PWM?")
        c.__dict__.setdefault("_fam_used", set()).add(f)
        return f
    if role in _PAIR:
        cache = c.__dict__.setdefault("_fam_inst", {})
        kind = "uart" if role in ("rx", "tx") else "i2c" if role in ("scl", "sda") else "spi"
        key = (b["id"], kind)
        if key not in cache:
            mine = [(pb["pin"], pb["role"]) for pb in b["pins"].values() if pb["role"] in _PAIR and
                    (("uart" if pb["role"] in ("rx", "tx") else "i2c" if pb["role"] in ("scl", "sda") else "spi") == kind)]
            cands = sorted({_inst_of(f) for f in valid if _inst_of(f) and f.startswith(_PAIR[role][0] if _PAIR[role][0] != "U" else "U")
                            and f == _fn_for(role, _inst_of(f))})
            ok = [i for i in cands if all(_fn_for(r, i) in c.pins.get(pn, {}).get("fns", []) for pn, r in mine)]
            if fam == "esp32" and kind == "uart":
                ok = [i for i in ok if i != "0"] or ok
                ok.sort(reverse=True)             # UART2 first (UART0 = USB console / Serial)
            cache[key] = (ok or cands or ["1"])[0]
        return _fn_for(role, cache[key])
    return "GPIO_IO"


def rail(pw: str | None) -> float | None:
    return {"v33": 3.3, "v5": 5.0, "vin": None, "gnd": 0.0}.get(pw or "", None)


def erc_extra(c) -> None:
    """Family-specific rules run after the generic ERC."""
    b = board(c.device)
    fam = b["family"]
    lim = limits(c.device)
    used = {num: fn for num, fn in c.assign.items()}
    for num, fn in used.items():
        p = c.pins.get(num, {})
        lab = c.label(num)
        if fam == "esp32":
            note = p.get("note", "")
            g = p.get("gpio")
            if "SPI flash" in note:
                c.add("error", "ESP_FLASH_PIN", f"{lab} is wired to the module's SPI flash - using it crashes the chip "
                      "(ESP32 datasheet §2.2 / module schematic).", [], [num])
            if "input only" in note and fn not in ("GPIO_IN",) and not fn.startswith(("ADC", "U1RX", "U2RX", "U0RX", "SDI")):
                c.add("error", "ESP_INPUT_ONLY", f"{lab} is input-only (GPIO34-39 have no output driver) but is used as {fn}.",
                      [], [num])
            if "input only" in note and fn == "GPIO_IN":
                c.add("info", "ESP_NO_PULL", f"{lab}: GPIO34-39 have no internal pull-up/down - add an external resistor "
                      "if the source can float.", [], [num])
            if "strapping" in note:
                c.add("warning", "ESP_STRAPPING", f"{lab} is a strapping pin (boot mode / flash voltage at reset): keep "
                      "external loads from forcing its level during reset (ESP32 datasheet §2.4 Strapping Pins).", [], [num])
            if fn.startswith("ADC2"):
                c.add("warning", "ESP_ADC2_WIFI", f"{lab} uses ADC2: ADC2 reads fail while WiFi is running - prefer an "
                      "ADC1 pin (GPIO32-39 on ESP32, GPIO0-4 on C3).", [], [num])
            if g in (1, 3) and b["id"] == "ESP32-DEVKITC" and fn != "U0TX" and fn != "U0RX":
                c.add("warning", "ESP_CONSOLE", f"{lab} is UART0 (USB-serial console / uploads) on the DevKitC.", [], [num])
        if fam == "avr" and p.get("label") in ("D0", "D1"):
            c.add("warning" if not fn.startswith("U0") else "info", "AVR_USB_SERIAL",
                  f"{lab} is shared with the USB-serial chip (Serial / sketch upload): keep it free or disconnect the "
                  "load while uploading.", [], [num])
        if b["id"] == "NUCLEO-F411RE" and p.get("label") in ("D0", "D1"):
            c.add("warning" if not fn.startswith("U2") else "info", "NUCLEO_VCP",
                  f"{lab} is wired to the ST-LINK virtual COM port (USART2) through SB13/SB14 (UM1724 §6.8): "
                  "other loads fight the ST-LINK TX unless the solder bridges are opened.", [], [num])
        if fam == "stm32" and p.get("port") in ("PA13", "PA14"):
            c.add("warning", "STM_SWD", f"{lab} is SWDIO/SWCLK: reusing it locks out the debugger after reset.", [], [num])
        if fam == "stm32" and p.get("port") in ("PC13", "PC14", "PC15") and b["mcu"] == "STM32F103C8":
            c.add("info", "STM_PC13", f"{lab}: PC13-15 sink only 3 mA at 2 MHz max (DS5319 §5.3.13 note) - fine for "
                  "the on-board LED, not for loads.", [], [num])
        if fn in ("SCK1",) and fam == "avr":
            ss = {"ATMEGA328P": "D10", "ATMEGA2560": "D53"}[b["mcu"]]
            c.add("info", "AVR_SPI_SS", f"SPI master: {ss} (SS) is driven as an output by the HAL - if it were an input "
                  "pulled low the SPI would drop to slave mode (ATmega datasheet §19.3.2).", [], [num])
    for bd in c.bindings:
        for pb in bd["pins"].values():
            pin = c.pins.get(pb["pin"], {})
            if fam == "stm32" and b["mcu"] == "STM32F103C8" and pin.get("port") in ("PC13", "PC14", "PC15") \
                    and bd.get("current_ma", 0) > 3:
                c.add("warning", "STM_PC13", f"{bd['id']}: {bd['current_ma']} mA from {pin['port']} exceeds the 3 mA "
                      "PC13-15 limit (DS5319 §5.3.13).", [bd["id"]], [pb["pin"]])
    if (c.d.get("power") or "board") == "board":
        c.add("info", "POWER_BOARD", f"{b['title']}: supply, decoupling, reset and clock come from the dev board. "
              f"Pins run at {lim['vdd']:g} V; per-pin abs. max {lim['abs']:g} mA ({lim['ref_abs']}).", [], [])
