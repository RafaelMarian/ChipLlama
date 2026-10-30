"""Writes examples/circuit/*.json (run from the repo root). Endpoints use real pin numbers from the bundled pinouts."""
import json
from pathlib import Path
from chipllama import pinout as PO

OUT = Path("examples/circuit")


def pn(dev, name):
    for p in PO.get(dev)["pins"]:
        if name in p["names"]:
            return str(p["num"])
    raise KeyError(f"{dev}: {name}")


def make(name, title, dev, parts, wires, **kw):
    u = lambda n: "U1." + pn(dev, n)
    ps = [{"id": "U1", "type": "mcu", "x": 420, "y": 60}]
    for i, (pid, t, params, x, y) in enumerate(parts):
        ps.append({"id": pid, "type": t, "params": params, "x": x, "y": y})
    ws = [{"a": u(a[1:]) if a.startswith("@") else a, "b": u(b[1:]) if b.startswith("@") else b} for a, b in wires]
    d = {"title": title, "name": name, "device": dev, "parts": ps, "wires": ws, **kw}
    (OUT / f"{name}.json").write_text(json.dumps(d, indent=1))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    G = "PIC24FJ256GB110"
    make("led_button", "LED on RA0 + button on RD6 (internal pull-up)", G,
         [("R1", "resistor", {"ohms": 330}, 120, 120), ("LED1", "led", {"color": "red"}, 40, 200), ("SW1", "button", {}, 120, 360),
          ("GND", "gnd", {}, 40, 480)],
         [("@RA0", "R1.1"), ("R1.2", "LED1.A"), ("LED1.K", "GND.-"), ("@RD6", "SW1.1"), ("SW1.2", "GND.-")])
    make("servo_pot", "Servo on OC1 (RP2/RD8) follows a pot on AN2", G,
         [("POT1", "pot", {}, 80, 140), ("SV1", "servo", {}, 80, 340), ("V5", "v5", {}, 40, 40), ("VDD", "vdd33", {}, 200, 40),
          ("GND", "gnd", {}, 40, 520)],
         [("@RB2", "POT1.W"), ("POT1.1", "GND.-"), ("POT1.2", "VDD.+"), ("@RD8", "SV1.SIG"), ("SV1.V+", "V5.+"), ("SV1.GND", "GND.-")])
    make("i2c_logger", "TMP102 on I2C2 logged over UART1 (RP17 TX / RP10 RX)", G,
         [("S1", "tmp102", {"pullups": True}, 60, 120), ("USB1", "uart_usb", {}, 60, 380), ("VDD", "vdd33", {}, 200, 30),
          ("GND", "gnd", {}, 40, 540)],
         [("@RA2", "S1.SCL"), ("@RA3", "S1.SDA"), ("S1.VCC", "VDD.+"), ("S1.GND", "GND.-"),
          ("@RF5", "USB1.RX"), ("@RF4", "USB1.TX"), ("USB1.GND", "GND.-")])
    make("gb110_multi", "GB110: LED, button, pot, servo, buzzer, TMP102, UART, LCD1602, SPI flash", G,
         [("R1", "resistor", {"ohms": 330}, 60, 60), ("LED1", "led", {}, 20, 140), ("SW1", "button", {}, 60, 230),
          ("POT1", "pot", {}, 60, 320), ("SV1", "servo", {}, 60, 430), ("BZ1", "buzzer_passive", {}, 60, 540),
          ("S1", "tmp102", {"pullups": True}, 1040, 60), ("USB1", "uart_usb", {}, 1040, 220), ("LCD1", "lcd1602", {}, 1040, 360),
          ("F1", "flash25", {}, 1040, 560), ("VDD", "vdd33", {}, 240, 0), ("V5", "v5", {}, 320, 0), ("GND", "gnd", {}, 240, 660)],
         [("@RA0", "R1.1"), ("R1.2", "LED1.A"), ("LED1.K", "GND.-"), ("@RD6", "SW1.1"), ("SW1.2", "GND.-"),
          ("@RB2", "POT1.W"), ("POT1.1", "GND.-"), ("POT1.2", "VDD.+"), ("@RD8", "SV1.SIG"), ("SV1.V+", "V5.+"), ("SV1.GND", "GND.-"),
          ("@RD9", "BZ1.+"), ("BZ1.-", "GND.-"), ("@RA2", "S1.SCL"), ("@RA3", "S1.SDA"), ("S1.VCC", "VDD.+"), ("S1.GND", "GND.-"),
          ("@RF5", "USB1.RX"), ("@RF4", "USB1.TX"), ("USB1.GND", "GND.-"),
          ("@RE0", "LCD1.RS"), ("@RE1", "LCD1.E"), ("@RE2", "LCD1.D4"), ("@RE3", "LCD1.D5"), ("@RE4", "LCD1.D6"), ("@RE5", "LCD1.D7"),
          ("LCD1.VCC", "V5.+"), ("LCD1.GND", "GND.-"),
          ("@RD1", "F1.SCK"), ("@RD2", "F1.SI"), ("@RD3", "F1.SO"), ("@RD4", "F1.CS"), ("F1.VCC", "VDD.+"), ("F1.GND", "GND.-")])
    K = "PIC18F45K22"
    make("k22_logger", "PIC18F45K22: LED, button, pot, TMP102 (MSSP1), EUSART1 logger, CCP1 buzzer", K,
         [("R1", "resistor", {"ohms": 330}, 60, 60), ("LED1", "led", {}, 20, 140), ("SW1", "button", {}, 60, 230),
          ("POT1", "pot", {}, 60, 330), ("BZ1", "buzzer_passive", {}, 60, 460), ("S1", "tmp102", {"pullups": True}, 820, 80),
          ("USB1", "uart_usb", {}, 820, 300), ("VDD", "vdd33", {}, 240, 0), ("GND", "gnd", {}, 240, 600)],
         [("@RD0", "R1.1"), ("R1.2", "LED1.A"), ("LED1.K", "GND.-"), ("@RB0", "SW1.1"), ("SW1.2", "GND.-"),
          ("@RA0", "POT1.W"), ("POT1.1", "GND.-"), ("POT1.2", "VDD.+"), ("@RC2", "BZ1.+"), ("BZ1.-", "GND.-"),
          ("@RC3", "S1.SCL"), ("@RC4", "S1.SDA"), ("S1.VCC", "VDD.+"), ("S1.GND", "GND.-"),
          ("@RC6", "USB1.RX"), ("@RC7", "USB1.TX"), ("USB1.GND", "GND.-")])


if __name__ == "__main__":
    main()
