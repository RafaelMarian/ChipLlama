"""Regenerate examples/blocks/*.json (the 5 Blocks-tab examples)."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "examples" / "blocks"


def B(t, fields=None, inputs=None):
    b = {"type": t}
    if fields:
        b["fields"] = fields
    if inputs:
        b["inputs"] = {k: {"block": v} for k, v in inputs.items()}
    return b


def seq(*bs):
    for a, b in zip(bs, bs[1:]):
        a["next"] = {"block": b}
    return bs[0]


def num(n):
    return B("math_number", {"NUM": n})


def V(n):
    return {"id": "var_" + n}


def ws(*tops, vars=()):
    for i, t in enumerate(tops):
        t["x"], t["y"] = 20 + i * 360, 20
    return {"blocks": {"languageVersion": 0, "blocks": list(tops)}, "variables": [{"name": v, "id": "var_" + v} for v in vars]}


EX = {
    "blink": ("Blink RA0 every 500 ms (Timer1 interrupt)",
              ws(B("cl_every", {"MS": 500}, {"DO": B("cl_pin_toggle", {"PIN": "RA0"})}))),
    "button_led": ("Button on RD6 (pull-up, change notification) toggles the LED on RA0", ws(
        B("cl_on_start", None, {"DO": B("cl_pin_mode", {"PIN": "RA0", "MODE": "OUTPUT"})}),
        B("cl_on_pin_change", {"PIN": "RD6", "PULLUP": "TRUE"}, {"DO": B("controls_if", None, {
            "IF0": B("logic_negate", None, {"BOOL": B("cl_pin_read", {"PIN": "RD6"})}),
            "DO0": B("cl_pin_toggle", {"PIN": "RA0"})})}))),
    "uart_hello": ("UART1 115200 on RP10/RP17, print hello every second", ws(
        B("cl_on_start", None, {"DO": B("cl_uart_begin", {"BAUD": 115200, "TX": 10, "RX": 17})}),
        B("cl_forever", None, {"DO": seq(B("cl_uart_println", {"TEXT": "hello from ChipLlama"}), B("cl_delay_ms", {"MS": 1000}))}))),
    "adc_logger": ("Read AN5 and print the value over UART every 200 ms", ws(
        B("cl_on_start", None, {"DO": B("cl_uart_begin", {"BAUD": 115200, "TX": 10, "RX": 17})}),
        B("cl_forever", None, {"DO": seq(
            B("variables_set", {"VAR": V("level")}, {"VALUE": B("cl_adc_read", {"CH": 5})}),
            B("cl_uart_print_num", {"NL": "TRUE"}, {"VALUE": B("variables_get", {"VAR": V("level")})}),
            B("cl_delay_ms", {"MS": 200}))}), vars=["level"])),
    "spi_i2c": ("SPI1 1 MHz mode 0 + I2C1 400 kHz: send a byte on each bus every 100 ms", ws(
        B("cl_on_start", None, {"DO": seq(B("cl_spi_begin", {"KHZ": 1000, "MODE": 0, "SCK": 10, "SDO": 17, "SDI": 16}),
                                         B("cl_i2c_begin", {"KHZ": 400}))}),
        B("cl_forever", None, {"DO": seq(B("cl_spi_send", None, {"VALUE": num(0xAA)}),
                                        B("cl_i2c_write", {"ADDR": "0x48"}, {"VALUE": num(1)}),
                                        B("cl_delay_ms", {"MS": 100}))}))),
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for k, (title, w) in EX.items():
        w["title"] = title
        (OUT / f"{k}.json").write_text(json.dumps(w, indent=1) + "\n")
