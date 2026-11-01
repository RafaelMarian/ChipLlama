"""Generate the M8 per-family Circuit examples (blink, button, UART, ADC, servo, I2C sensor) for each family board.

python scripts/make_family_examples.py   -> examples/circuit/families/<board>_<kind>.json
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chipllama.families import boards  # noqa: E402
from chipllama import geometry as GM, schematic as sch  # noqa: E402

OUT = ROOT / "examples" / "circuit" / "families"
# pin choices per board: led, button, tx, rx, adc, pwm, scl, sda
PICK = {
    "ARDUINO-UNO": dict(led="D13", btn="D2", tx="D1", rx="D0", adc="A0", pwm="D9", scl="A5", sda="A4", io=5.0),
    "ARDUINO-NANO": dict(led="D13", btn="D2", tx="D1", rx="D0", adc="A0", pwm="D9", scl="A5", sda="A4", io=5.0),
    "ARDUINO-MEGA2560": dict(led="D13", btn="D2", tx="D18", rx="D19", adc="A0", pwm="D11", scl="D21", sda="D20", io=5.0),
    "STM32-BLUEPILL": dict(led="PB12", btn="PB0", tx="PA9", rx="PA10", adc="PA0", pwm="PA8", scl="PB6", sda="PB7", io=3.3),
    "NUCLEO-F411RE": dict(led="D7", btn="D2", tx="D8", rx="D2", adc="A0", pwm="D9", scl="D15", sda="D14", io=3.3),
    "STM32F030F4P6": dict(led="PA4", btn="PA0", tx="PA2", rx="PA3", adc="PA1", pwm="PA6", scl="PA9", sda="PA10", io=3.3),
    "ESP32-DEVKITC": dict(led="GPIO23", btn="GPIO27", tx="GPIO17", rx="GPIO16", adc="GPIO34", pwm="GPIO18", scl="GPIO22",
                          sda="GPIO21", io=3.3),
    "ESP32-C3-DEVKITM": dict(led="GPIO7", btn="GPIO6", tx="GPIO21", rx="GPIO20", adc="GPIO1", pwm="GPIO10", scl="GPIO5",
                             sda="GPIO4", io=3.3),
}
TITLES = {"blink": "Blink LED", "button": "Button toggles LED", "uart": "UART echo / hello", "adc": "Potentiometer on ADC",
          "servo": "Servo sweep (PWM)", "i2c": "TMP102 I2C temperature"}


def make(bid: str, kind: str) -> dict:
    b = boards()[bid]
    pk = PICK[bid]
    num = {p["label"]: p["num"] for p in b["pins"] if not p.get("power")}
    pw = {}
    for p in b["pins"]:
        pw.setdefault(p["power"], p["num"]) if p.get("power") else None
    parts = [{"id": "U1", "type": "mcu", "x": 0, "y": 0, "rot": 0, "params": {}}]
    wires = []
    k = {"n": 0}

    def part(pid, typ, params=None):
        k["n"] += 1
        parts.append({"id": pid, "type": typ, "x": -320 - 160 * ((k["n"] - 1) // 6), "y": 40 + 110 * ((k["n"] - 1) % 6),
                      "rot": 0, "params": params or {}})

    def w(a, c):
        wires.append({"a": a, "b": c, "pts": []})

    def gnd(endpoint):
        g = f"PWR{len([p for p in parts if p['id'].startswith('PWR')]) + 1}"
        part(g, "gnd")
        w(endpoint, f"{g}.1")

    def rail(endpoint, v):
        g = f"PWR{len([p for p in parts if p['id'].startswith('PWR')]) + 1}"
        part(g, "v5" if v > 4 else "vdd33")
        w(endpoint, f"{g}.1")

    gnd(f"U1.{pw['gnd']}")
    U = lambda lab: f"U1.{num[lab]}"  # noqa: E731
    if kind in ("blink", "button"):
        part("R1", "resistor", {"ohms": 330 if pk["io"] < 4 else 470})
        part("LED1", "led", {"color": "red", "vf": 2.0, "if_ma": 5})
        w(U(pk["led"]), "R1.1")
        w("R1.2", "LED1.2")
        gnd("LED1.1")
    if kind == "button":
        part("SW1", "button", {"pullup": "internal", "debounce_ms": 20})
        w(U(pk["btn"]), "SW1.1")
        gnd("SW1.3")
    if kind == "uart":
        part("USB1", "uart_usb", {"baud": 115200, "io_v": pk["io"]})
        w(U(pk["tx"]), "USB1.2")
        w(U(pk["rx"]), "USB1.3")
        gnd("USB1.1")
    if kind == "adc":
        part("RV1", "pot", {"ohms": 10000})
        gnd("RV1.1")
        w(U(pk["adc"]), "RV1.2")
        rail("RV1.3", pk["io"])
    if kind == "servo":
        part("M1", "servo", {"min_us": 1000, "max_us": 2000})
        gnd("M1.1")
        rail("M1.2", 5.0)
        w(U(pk["pwm"]), "M1.3")
    if kind == "i2c":
        part("S1", "tmp102", {"addr": "0x48", "pullups": False})
        part("R10", "resistor", {"ohms": 4700})
        part("R11", "resistor", {"ohms": 4700})
        rail("S1.1", 3.3)
        gnd("S1.2")
        w(U(pk["scl"]), "S1.3")
        w(U(pk["sda"]), "S1.4")
        w("R10.2", "S1.3")
        w("R11.2", "S1.4")
        rail("R10.1", 3.3)
        rail("R11.1", 3.3)
    return {"schema": 2, "name": f"{bid.lower().replace('-', '_')}_{kind}", "title": f"{b['title'].split(' (')[0]}: {TITLES[kind]}",
            "device": bid, "family": b["family"], "power": "board", "parts": parts, "wires": wires, "assign": {}}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    for bid in PICK:
        for kind in TITLES:
            d = GM.tidy(sch.migrate(make(bid, kind)))
            a = GM.audit(d)
            if a["problems"] or d.get("unrouted"):
                print("GEOMETRY", d["name"], d.get("unrouted"), a["problems"][:4])
            (OUT / f"{d['name']}.json").write_text(json.dumps(d, indent=1))
            n += 1
    print(f"wrote {n} examples to {OUT}")


if __name__ == "__main__":
    main()
