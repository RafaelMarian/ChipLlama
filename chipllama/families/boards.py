"""Board pin tables for the M8 families (Arduino AVR, STM32, ESP32).

Every MCU signal comes from vendor data (see scripts/build_family_data.py):
  AVR   : Microchip ATDF <signal pad=...> (USART/SPI/TWI/ADC/TC per pad)
  STM32 : STM32_open_pin_data mcu/*.xml <Signal> + IP/GPIO-* AF numbers
  ESP32 : Espressif ESP32 / ESP32-C3 datasheets (IO_MUX + GPIO matrix tables, section 2/3)
The header names (D13, A0, PA5, GPIO2 ...) and pad -> header wiring are the board makers' published
schematics: Arduino Uno R3 / Nano 3 / Mega 2560 R3, Blue Pill (STM32F103C8T6 min. system board),
ST UM1724 (Nucleo-64, Arduino + morpho headers), Espressif ESP32-DevKitC V4 / ESP32-C3-DevKitM-1.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "families"

# ------------------------------------------------------------------ AVR boards
_UNO_D = ["PD0", "PD1", "PD2", "PD3", "PD4", "PD5", "PD6", "PD7", "PB0", "PB1", "PB2", "PB3", "PB4", "PB5"]
_UNO_A = ["PC0", "PC1", "PC2", "PC3", "PC4", "PC5"]
_MEGA_D = ["PE0", "PE1", "PE4", "PE5", "PG5", "PE3", "PH3", "PH4", "PH5", "PH6", "PB4", "PB5", "PB6", "PB7", "PJ1", "PJ0",
           "PH1", "PH0", "PD3", "PD2", "PD1", "PD0", "PA0", "PA1", "PA2", "PA3", "PA4", "PA5", "PA6", "PA7", "PC7", "PC6",
           "PC5", "PC4", "PC3", "PC2", "PC1", "PC0", "PD7", "PG2", "PG1", "PG0", "PL7", "PL6", "PL5", "PL4", "PL3", "PL2",
           "PL1", "PL0", "PB3", "PB2", "PB1", "PB0"]
_MEGA_A = ["PF0", "PF1", "PF2", "PF3", "PF4", "PF5", "PF6", "PF7", "PK0", "PK1", "PK2", "PK3", "PK4", "PK5", "PK6", "PK7"]

POWER_TOP = [("5V", "v5"), ("3V3", "v33"), ("VIN", "vin")]
POWER_BOT = [("GND", "gnd"), ("GND", "gnd")]


def _avr_fns(sigs: list[str]) -> tuple[list[str], int | None]:
    fns, an = [], None
    for s in sigs:
        inst, sig = s.split(":", 1)
        m = re.fullmatch(r"USART(\d)", inst)
        if m and sig in ("TXD", "RXD"):
            fns.append(f"U{m.group(1)}{'TX' if sig == 'TXD' else 'RX'}")
        elif inst == "SPI" and sig in ("SCK", "MOSI", "MISO", "SS"):
            fns.append({"SCK": "SCK1", "MOSI": "SDO1", "MISO": "SDI1", "SS": "SS1"}[sig])
        elif inst == "TWI" and sig in ("SDA", "SCL"):
            fns.append(sig + "1")
        elif inst == "ADC" and re.fullmatch(r"ADC\d+", sig):
            an = int(sig[3:])
            fns.append(f"AN{an}")
        elif re.fullmatch(r"TC([1-5])", inst) and re.fullmatch(r"OC[ABC]", sig):
            fns.append(f"OC{inst[2]}{sig[2]}")          # Timer0 is the 1 ms tick -> OC0x not offered
        elif inst == "TC2" and sig in ("OCA", "OCB"):
            fns.append(f"OC2{sig[2]}")
    return fns, an


def _avr_board(bid, title, mcu, dnames, anames, extra_an=(), layout=None):
    d = json.loads((DATA / "avr" / f"{mcu}.json").read_text())
    pins, num = [], 0
    rows = layout or {"R": [f"D{i}" for i in range(len(dnames))], "L": [f"A{i}" for i in range(len(anames) + len(extra_an))]}
    hdr = {f"D{i}": p for i, p in enumerate(dnames)}
    for i, p in enumerate(anames):
        hdr[f"A{i}"] = p
    for side, labels in rows.items():
        for r, lab in enumerate(labels):
            num += 1
            port = hdr.get(lab)
            if port is None:            # A6/A7 on the Nano: ADC-only, no port
                ch = int(lab[1:])
                pins.append({"num": num, "label": lab, "names": [lab, f"ADC{ch}"], "port": None, "power": None, "an": ch,
                             "fns": [f"AN{ch}"], "pullup": False, "tol5v": True, "side": side, "row": r,
                             "note": "analog input only (no digital buffer)"})
                continue
            sigs = d["pads"].get(port, [])
            fns, an = _avr_fns(sigs)
            names = [lab, port] + [s.split(":", 1)[1] for s in sigs if not s.startswith(("PORT", "EXINT:PCINT"))]
            pins.append({"num": num, "label": lab, "names": names, "port": port, "power": None, "an": an,
                         "fns": ["GPIO_OUT", "GPIO_IN", "GPIO_IO"] + fns, "pullup": True, "tol5v": True,
                         "side": side, "row": r})
    _power(pins, POWER_TOP, POWER_BOT)
    return {"id": bid, "title": title, "family": "avr", "mcu": mcu, "vdd": 5.0, "fcpu": 16000000, "pins": pins}


def _power(pins, top, bot):
    num = len(pins)
    for i, (lab, pw) in enumerate(top):
        num += 1
        pins.append({"num": num, "label": lab, "names": [lab], "port": None, "power": pw, "an": None, "fns": [],
                     "side": "T", "row": i, "tol5v": None})
    for i, (lab, pw) in enumerate(bot):
        num += 1
        pins.append({"num": num, "label": lab, "names": [lab], "port": None, "power": pw, "an": None, "fns": [],
                     "side": "B", "row": i, "tol5v": None})


# ------------------------------------------------------------------ STM32 boards
_F1_NOT_FT = {"PA0", "PA1", "PA2", "PA3", "PA4", "PA5", "PA6", "PA7", "PB0", "PB1", "PB5", "PC13", "PC14", "PC15"}
_F0_NOT_FT = {"PA0", "PA1", "PA2", "PA3", "PA4", "PA5", "PA6", "PA7", "PB1"}


def _stm_fns(p: dict, f1: bool = False) -> tuple[list[str], dict, int | None]:
    fns, af, an = [], {}, None
    for s in p["signals"]:
        a = p["af"].get(s, "default" if f1 else None)   # F1: no AF mux, unlisted = default mapping
        if s.startswith("ADC") and re.search(r"_IN(\d+)$", s):
            if s.startswith(("ADC1_", "ADC_")):
                an = int(re.search(r"_IN(\d+)$", s).group(1))
                fns.append(f"AN{an}")
            continue
        if a is None or (isinstance(a, str) and a != "default"):
            continue                                           # remapped (F1) functions are not offered
        f = None
        m = re.fullmatch(r"U(?:S)?ART(\d)_(TX|RX)", s)
        if m:
            f = f"U{m.group(1)}{m.group(2)}"
        m = re.fullmatch(r"I2C(\d)_(SCL|SDA)", s)
        if m:
            f = f"{m.group(2)}{m.group(1)}"
        m = re.fullmatch(r"SPI(\d)_(SCK|MOSI|MISO)", s)
        if m:
            f = {"SCK": "SCK", "MOSI": "SDO", "MISO": "SDI"}[m.group(2)] + m.group(1)
        m = re.fullmatch(r"TIM(\d+)_CH(\d)", s)
        if m:
            f = s
        if f and not _stm_ok(f):
            f = None
        if f:
            fns.append(f)
            af[f] = a if isinstance(a, int) else None
    return fns, af, an


def _stm_ok(f: str) -> bool:
    """Only instances the generated HAL implements (and that every listed part has)."""
    m = re.fullmatch(r"U(\d)(TX|RX)", f)
    if m:
        return m.group(1) in "1236"
    m = re.fullmatch(r"(SCL|SDA|SCK|SDO|SDI)(\d)", f)
    if m:
        return m.group(2) in "123"
    m = re.fullmatch(r"TIM(\d+)_CH\d", f)
    return bool(m) and int(m.group(1)) in (1, 2, 3, 4, 5, 9, 10, 11, 14, 15, 16, 17)


@lru_cache(maxsize=8)
def stm32_data(mcu: str) -> dict:
    return json.loads((DATA / "stm32" / f"{mcu}.json").read_text())


def _stm_board(bid, title, mcu, layout: dict, hdr_map: dict | None = None, notes: dict | None = None, top=None, bot=None,
               not_ft=None, fcpu=72000000):
    d = stm32_data(mcu)
    by = {p["port"]: p for p in d["pins"] if p["port"]}
    pins, num = [], 0
    for side, labels in layout.items():
        for r, lab in enumerate(labels):
            num += 1
            port = (hdr_map or {}).get(lab, lab)
            p = by[port]
            fns, af, an = _stm_fns(p, d["series"] == "f1")
            tol = None if not_ft is None else port not in not_ft
            if an is not None:
                pass
            names = ([lab, port] if lab != port else [port]) + p["signals"][:10]
            pins.append({"num": num, "label": lab, "names": names, "port": port, "power": None, "an": an,
                         "fns": ["GPIO_OUT", "GPIO_IN", "GPIO_IO"] + fns, "af": af, "pullup": True, "tol5v": tol,
                         "side": side, "row": r, "note": (notes or {}).get(lab, "")})
    _power(pins, top or [("3V3", "v33"), ("5V", "v5")], bot or [("GND", "gnd"), ("GND", "gnd")])
    return {"id": bid, "title": title, "family": "stm32", "mcu": mcu, "vdd": 3.3, "fcpu": fcpu, "pins": pins}


# ------------------------------------------------------------------ ESP32 boards
def _esp_pin(num, side, row, g, chip):
    if chip == "esp32":
        adc1 = {36: 0, 37: 1, 38: 2, 39: 3, 32: 4, 33: 5, 34: 6, 35: 7}
        adc2 = {4: 0, 0: 1, 2: 2, 15: 3, 13: 4, 12: 5, 14: 6, 27: 7, 25: 8, 26: 9}
        inonly, flash, strap = range(34, 40), range(6, 12), (0, 2, 5, 12, 15)
        names = {1: "TX0", 3: "RX0", 36: "VP", 39: "VN", 21: "SDA", 22: "SCL", 18: "SCK", 19: "MISO", 23: "MOSI", 5: "SS",
                 17: "TX2", 16: "RX2", 25: "DAC1", 26: "DAC2"}
    else:
        adc1 = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}
        adc2 = {5: 0}
        inonly, flash, strap = (), range(12, 18), (2, 8, 9)
        names = {21: "TX", 20: "RX", 8: "SDA/LED", 9: "SCL/BOOT", 4: "SCK", 5: "MISO", 6: "MOSI", 7: "SS", 18: "USB_D-",
                 19: "USB_D+"}
    fns = ["GPIO_IN"] if g in inonly else ["GPIO_OUT", "GPIO_IN", "GPIO_IO"]
    if g in adc1:
        fns.append(f"ADC1_CH{adc1[g]}")
    if g in adc2:
        fns.append(f"ADC2_CH{adc2[g]}")
    if g not in inonly and g not in flash:
        # GPIO matrix: any output-capable pad can carry UART/I2C/SPI/LEDC
        fns += ["U1TX", "U2TX", "SCL1", "SDA1", "SCK1", "SDO1", "LEDC"] if chip == "esp32" else ["U1TX", "SCL1", "SDA1", "SCK1", "SDO1", "LEDC"]
        if g == (1 if chip == "esp32" else 21):
            fns.insert(3, "U0TX")
    if g not in flash:
        fns += ["U1RX", "U2RX", "SDI1"] if chip == "esp32" else ["U1RX", "SDI1"]
        if g == (3 if chip == "esp32" else 20):
            fns.append("U0RX")
    note = []
    if g in inonly:
        note.append("input only (no output driver, no pull-up/down)")
    if g in flash:
        note.append("connected to the SPI flash - do not use")
    if g in strap:
        note.append("strapping pin (boot mode) - avoid pulling it at reset")
    if g in adc2:
        note.append("ADC2: unusable while WiFi is on")
    lab = f"GPIO{g}"
    return {"num": num, "label": lab, "names": [lab] + ([names[g]] if g in names else []), "port": lab, "gpio": g,
            "power": None, "an": g if (g in adc1 or g in adc2) else None, "fns": fns, "pullup": g not in inonly,
            "tol5v": False, "side": side, "row": row, "note": "; ".join(note)}


def _esp_board(bid, title, chip, left, right, fcpu):
    pins, num = [], 0
    top, bot = [], []
    for side, col in (("L", left), ("R", right)):
        r = 0
        for x in col:
            if isinstance(x, int):
                num += 1
                pins.append(_esp_pin(num, side, r, x, chip))
                r += 1
            elif x in ("GND",):
                if ("GND", "gnd") not in bot:
                    bot.append(("GND", "gnd"))
            elif x in ("3V3", "5V"):
                if (x, "v33" if x == "3V3" else "v5") not in top:
                    top.append((x, "v33" if x == "3V3" else "v5"))
            # EN / RST / CLK are not offered
    _power(pins, top, bot)
    return {"id": bid, "title": title, "family": "esp32", "mcu": chip.upper(), "chip": chip, "vdd": 3.3, "fcpu": fcpu, "pins": pins}


@lru_cache(maxsize=1)
def boards() -> dict:
    b = {}
    b["ARDUINO-UNO"] = _avr_board("ARDUINO-UNO", "Arduino Uno R3 (ATmega328P, 16 MHz, 5 V)", "ATMEGA328P", _UNO_D, _UNO_A)
    b["ARDUINO-NANO"] = _avr_board("ARDUINO-NANO", "Arduino Nano 3 (ATmega328P, 16 MHz, 5 V)", "ATMEGA328P", _UNO_D, _UNO_A,
                                   layout={"R": [f"D{i}" for i in range(14)], "L": [f"A{i}" for i in range(8)]})
    b["ARDUINO-MEGA2560"] = _avr_board("ARDUINO-MEGA2560", "Arduino Mega 2560 R3 (ATmega2560, 16 MHz, 5 V)", "ATMEGA2560",
                                       _MEGA_D, _MEGA_A,
                                       layout={"R": [f"D{i}" for i in range(0, 38)],
                                               "L": [f"D{i}" for i in range(38, 54)] + [f"A{i}" for i in range(16)]})
    b["STM32-BLUEPILL"] = _stm_board(
        "STM32-BLUEPILL", "Blue Pill (STM32F103C8T6, 8 MHz HSE -> 72 MHz, 3.3 V)", "STM32F103C8",
        {"L": ["PB12", "PB13", "PB14", "PB15", "PA8", "PA9", "PA10", "PA11", "PA12", "PA15", "PB3", "PB4", "PB5", "PB6", "PB7",
               "PB8", "PB9"],
         "R": ["PC13", "PC14", "PC15", "PA0", "PA1", "PA2", "PA3", "PA4", "PA5", "PA6", "PA7", "PB0", "PB1", "PB10", "PB11"]},
        notes={"PC13": "on-board LED (active low); 3 mA max sink/source, 2 MHz (RM0008 / DS5319 p.27)",
               "PA11": "USB D-", "PA12": "USB D+ (1k5 pull-up on the board)", "PB3": "JTDO after reset (freed by the HAL)",
               "PB4": "NJTRST after reset (freed by the HAL)", "PA15": "JTDI after reset (freed by the HAL)"},
        top=[("3V3", "v33"), ("5V", "v5")], not_ft=_F1_NOT_FT, fcpu=72000000)
    nuc = {"D0": "PA3", "D1": "PA2", "D2": "PA10", "D3": "PB3", "D4": "PB5", "D5": "PB4", "D6": "PB10", "D7": "PA8", "D8": "PA9",
           "D9": "PC7", "D10": "PB6", "D11": "PA7", "D12": "PA6", "D13": "PA5", "D14": "PB9", "D15": "PB8",
           "A0": "PA0", "A1": "PA1", "A2": "PA4", "A3": "PB0", "A4": "PC1", "A5": "PC0"}
    b["NUCLEO-F411RE"] = _stm_board(
        "NUCLEO-F411RE", "Nucleo-F411RE (STM32F411RE, HSI -> 84 MHz, 3.3 V)", "STM32F411RE",
        {"R": [f"D{i}" for i in range(16)],
         "L": [f"A{i}" for i in range(6)] + ["PC13", "PC10", "PC12", "PB12", "PB13", "PB14", "PB15", "PB7", "PC6", "PC8", "PC9",
                                              "PA11", "PA12", "PA15", "PB2", "PB1", "PC2", "PC3"]},
        hdr_map=nuc, notes={"D0": "USART2 RX - wired to the ST-LINK virtual COM port (SB13/SB14)",
                            "D1": "USART2 TX - wired to the ST-LINK virtual COM port (SB13/SB14)",
                            "D13": "user LED LD2", "PC13": "user button B1 (active low, external pull-up)"},
        top=[("3V3", "v33"), ("5V", "v5")], not_ft=None, fcpu=84000000)
    b["STM32F030F4P6"] = _stm_board(
        "STM32F030F4P6", "STM32F030F4P6 TSSOP-20 module (HSI -> 48 MHz, 3.3 V)", "STM32F030F4",
        {"L": ["PA0", "PA1", "PA2", "PA3", "PA4", "PA5", "PA6"], "R": ["PA7", "PB1", "PA9", "PA10", "PA13", "PA14", "PF0", "PF1"]},
        notes={"PA13": "SWDIO", "PA14": "SWCLK - leave free for the debugger", "PF0": "OSC_IN", "PF1": "OSC_OUT"},
        top=[("3V3", "v33")], not_ft=_F0_NOT_FT, fcpu=48000000)
    b["ESP32-DEVKITC"] = _esp_board(
        "ESP32-DEVKITC", "ESP32-DevKitC V4 (ESP32-WROOM-32, 240 MHz, 3.3 V)", "esp32",
        ["3V3", 36, 39, 34, 35, 32, 33, 25, 26, 27, 14, 12, "GND", 13, 9, 10, 11, "5V"],
        ["GND", 23, 22, 1, 3, 21, "GND", 19, 18, 5, 17, 16, 4, 0, 2, 15, 8, 7, 6], 240000000)
    b["ESP32-C3-DEVKITM"] = _esp_board(
        "ESP32-C3-DEVKITM", "ESP32-C3-DevKitM-1 (RISC-V 160 MHz, 3.3 V)", "esp32c3",
        ["GND", "3V3", 2, 3, "GND", 0, 1, 10, "GND", "5V"],
        ["GND", 21, 20, "GND", 9, 8, "GND", 7, 6, 5, 4, "GND", 18, 19, "GND"], 160000000)
    return b


ALIASES = {"ATMEGA328P": "ARDUINO-UNO", "UNO": "ARDUINO-UNO", "ARDUINO_UNO": "ARDUINO-UNO", "NANO": "ARDUINO-NANO",
           "ATMEGA2560": "ARDUINO-MEGA2560", "MEGA2560": "ARDUINO-MEGA2560", "ARDUINO-MEGA": "ARDUINO-MEGA2560",
           "BLUEPILL": "STM32-BLUEPILL", "STM32F103C8": "STM32-BLUEPILL", "STM32F103C8T6": "STM32-BLUEPILL",
           "STM32F411RE": "NUCLEO-F411RE", "NUCLEO_F411RE": "NUCLEO-F411RE", "STM32F030F4": "STM32F030F4P6",
           "ESP32": "ESP32-DEVKITC", "ESP32-WROOM-32": "ESP32-DEVKITC", "ESP32-C3": "ESP32-C3-DEVKITM", "ESP32C3": "ESP32-C3-DEVKITM"}


def resolve(device: str) -> str | None:
    d = (device or "").upper().strip()
    if d in boards():
        return d
    return ALIASES.get(d)
