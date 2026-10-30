"""Writes examples/circuit/*.json (run from the repo root with PYTHONPATH=.).

Schema 2 (KiCad-style): wire endpoints are REF.<physical pin number>; MCU endpoints are U1.<package pin>.
Every example uses real symbols (4-pin button, 2-pin LED/resistor, ...), separate GND / +3V3 / +5V power symbols and
real resistors (LED series R, I2C pull-ups).  Examples marked mcu_power=True also get the MCU power block
(100 nF per VDD, 10 uF on VCAP, 10 k MCLR pull-up, ICSP header) from schematic.autowire().
"""
import json
from pathlib import Path
from chipllama import pinout as PO, symbols as S, schematic as SCH

OUT = Path("examples/circuit")
PWR = {"GND": "gnd", "3V3": "vdd33", "5V": "v5"}


def pn(dev, name):
    for p in PO.get(dev)["pins"]:
        if name in p["names"]:
            return str(p["num"])
    raise KeyError(f"{dev}: {name}")


def make(name, title, dev, parts, wires, mcu_power=False, auto=(), **kw):
    ps = [{"id": "U1", "type": "mcu", "x": 420, "y": 60, "params": {}}]
    pos = {}
    for pid, t, params, x, y, *rest in parts:
        ps.append({"id": pid, "type": t, "params": params, "x": x, "y": y, **({"rot": rest[0]} if rest else {})})
        pos[pid] = (x, y, t)
    npwr = [0]

    def ep(s, other):
        if s.startswith("@"):
            return "U1." + pn(dev, s[1:])
        if s in PWR:  # one power symbol per connection, like KiCad
            npwr[0] += 1
            opid, _, onum = other.partition(".")
            ox, oy, ot = pos.get(opid, (300, 300, ""))
            dy = 60 if s == "GND" else -60
            pid = f"PWR{npwr[0]}"
            ps.append({"id": pid, "type": PWR[s], "params": {}, "x": ox + 40 * (npwr[0] % 2), "y": oy + dy})
            return pid + ".1"
        pid, _, port = s.partition(".")
        t = pos[pid][2]
        if port.isdigit() and any(p["num"] == port for p in S.entry(t)["pins"]):
            return s
        nums = S.pins_of_port(t, port)
        if not nums:
            raise KeyError(f"{t}: no port {port}")
        return f"{pid}.{nums[0]}"

    ws = []
    for a, b in wires:
        A = ep(a, b) if a not in PWR else None
        B = ep(b, a if A is None else A) if b not in PWR else None
        if A is None:
            A = ep(a, B)
        if B is None:
            B = ep(b, A)
        ws.append({"a": A, "b": B})
    d = {"schema": 2, "title": title, "name": name, "device": dev, "parts": ps, "wires": ws, **kw}
    if mcu_power:
        d = SCH.autowire(d, "U1")["circuit"]
    for pid in auto:
        r = SCH.autowire(d, pid)
        assert r["ok"], r
        d = r["circuit"]
    (OUT / f"{name}.json").write_text(json.dumps(d, indent=1))
    return d


def i2c_pullups(sda, scl, x, y):
    parts = [("R10", "resistor", {"ohms": 4700}, x, y), ("R11", "resistor", {"ohms": 4700}, x + 40, y)]
    wires = [("R10.1", "3V3"), ("R10.2", scl), ("R11.1", "3V3"), ("R11.2", sda)]
    return parts, wires


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.json"):
        f.unlink()
    G = "PIC24FJ256GB110"
    make("led_button", "LED on RA0 (330 R) + 4-pin button on RD6 (internal pull-up)", G,
         [("R1", "resistor", {"ohms": 330}, 200, 160, 90), ("LED1", "led", {"color": "red"}, 100, 200),
          ("SW1", "button", {}, 200, 420)],
         [("@RA0", "R1.1"), ("R1.2", "LED1.A"), ("LED1.K", "GND"), ("@RD6", "SW1.1"), ("SW1.3", "GND")], mcu_power=True)
    make("servo_pot", "Servo on OC1 (RP2/RD8) follows a pot on AN2", G,
         [("POT1", "pot", {}, 160, 200), ("SV1", "servo", {}, 160, 440)],
         [("@RB2", "POT1.W"), ("POT1.1", "GND"), ("POT1.3", "3V3"), ("@RD8", "SV1.SIG"), ("SV1.V+", "5V"), ("SV1.GND", "GND")])
    pp, pw = i2c_pullups("S1.SDA", "S1.SCL", 200, 120)
    make("i2c_logger", "TMP102 on I2C2 (4.7 k pull-ups) logged over UART1 (RP17 TX / RP10 RX)", G,
         [("S1", "tmp102", {"pullups": False}, 120, 260), ("USB1", "uart_usb", {}, 120, 520), ("C1", "capacitor", {"farads": 1e-7}, 260, 260)] + pp,
         [("@RA2", "S1.SCL"), ("@RA3", "S1.SDA"), ("S1.VCC", "3V3"), ("S1.GND", "GND"), ("C1.1", "3V3"), ("C1.2", "GND"),
          ("@RF5", "USB1.RX"), ("@RF4", "USB1.TX"), ("USB1.GND", "GND")] + pw)
    pp, pw = i2c_pullups("S1.SDA", "S1.SCL", 1500, 80)
    make("gb110_multi", "GB110: LED, button, pot, servo, buzzer, TMP102, UART, LCD1602, SPI flash + MCU power", G,
         [("R1", "resistor", {"ohms": 330}, 260, 80, 90), ("LED1", "led", {}, 160, 120), ("SW1", "button", {}, 260, 280),
          ("POT1", "pot", {}, 260, 420), ("SV1", "servo", {}, 220, 600), ("BZ1", "buzzer_passive", {}, 260, 780),
          ("S1", "tmp102", {"pullups": False}, 1420, 160), ("USB1", "uart_usb", {}, 1420, 380), ("LCD1", "lcd1602", {}, 1420, 640),
          ("RV1", "pot", {}, 1560, 640), ("F1", "flash25", {}, 1420, 1000)] + pp,
         [("@RA0", "R1.1"), ("R1.2", "LED1.A"), ("LED1.K", "GND"), ("@RD6", "SW1.1"), ("SW1.3", "GND"),
          ("@RB2", "POT1.W"), ("POT1.1", "GND"), ("POT1.3", "3V3"), ("@RD8", "SV1.SIG"), ("SV1.V+", "5V"), ("SV1.GND", "GND"),
          ("@RD9", "BZ1.+"), ("BZ1.-", "GND"), ("@RA2", "S1.SCL"), ("@RA3", "S1.SDA"), ("S1.VCC", "3V3"), ("S1.GND", "GND"),
          ("@RF5", "USB1.RX"), ("@RF4", "USB1.TX"), ("USB1.GND", "GND"),
          ("@RE0", "LCD1.RS"), ("@RE1", "LCD1.E"), ("@RE2", "LCD1.D4"), ("@RE3", "LCD1.D5"), ("@RE4", "LCD1.D6"), ("@RE5", "LCD1.D7"),
          ("LCD1.VCC", "5V"), ("LCD1.GND", "GND"), ("LCD1.RW", "GND"), ("LCD1.A", "5V"), ("LCD1.K", "GND"),
          ("RV1.1", "GND"), ("RV1.3", "5V"), ("RV1.W", "LCD1.VO"),
          ("@RD1", "F1.SCK"), ("@RD2", "F1.SI"), ("@RD3", "F1.SO"), ("@RD4", "F1.CS"), ("F1.VCC", "3V3"), ("F1.GND", "GND")] + pw,
         mcu_power=True)
    make("autowired", "Auto-wired: relay (NPN + flyback), DHT22, active buzzer, LED - all placed by the Auto-wire button", G,
         [("K1", "relay", {}, 160, 160), ("S2", "dht22", {}, 160, 520), ("LED1", "led", {}, 160, 760)],
         [], mcu_power=True, auto=("K1", "S2", "LED1"))
    K = "PIC18F45K22"
    pp, pw = i2c_pullups("S1.SDA", "S1.SCL", 900, 60)
    make("k22_logger", "PIC18F45K22: LED, button, pot, TMP102 (MSSP1), EUSART1 logger, CCP1 buzzer", K,
         [("R1", "resistor", {"ohms": 330}, 260, 80, 90), ("LED1", "led", {}, 160, 120), ("SW1", "button", {}, 260, 280),
          ("POT1", "pot", {}, 260, 420), ("BZ1", "buzzer_passive", {}, 260, 600), ("S1", "tmp102", {"pullups": False}, 820, 160),
          ("USB1", "uart_usb", {}, 820, 400)] + pp,
         [("@RD0", "R1.1"), ("R1.2", "LED1.A"), ("LED1.K", "GND"), ("@RB0", "SW1.1"), ("SW1.3", "GND"),
          ("@RA0", "POT1.W"), ("POT1.1", "GND"), ("POT1.3", "3V3"), ("@RC2", "BZ1.+"), ("BZ1.-", "GND"),
          ("@RC3", "S1.SCL"), ("@RC4", "S1.SDA"), ("S1.VCC", "3V3"), ("S1.GND", "GND"),
          ("@RC6", "USB1.RX"), ("@RC7", "USB1.TX"), ("USB1.GND", "GND")] + pw)


if __name__ == "__main__":
    main()
