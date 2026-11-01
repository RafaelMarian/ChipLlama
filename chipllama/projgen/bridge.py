"""Circuit -> Blocks bridge: turn the wired circuit into a starter Blocks workspace (PIC24FJ256GB110 only,
because the Blocks generator targets that board). LEDs become outputs, the first button toggles the first LED
on change notification, a UART adapter gets cl_uart_begin with its RP numbers, a pot is read and printed."""
from __future__ import annotations

from .. import circuit as cir


def _chain(blocks):
    head = None
    for b in reversed(blocks):
        if head is not None:
            b["next"] = {"block": head}
        head = b
    return head


def to_blocks(d: dict, packs: str = "") -> dict:
    r = cir.check(d, packs)
    if str(r["device"]).upper() != "PIC24FJ256GB110":
        return {"ok": False, "reason": "Blocks targets PIC24FJ256GB110; the Circuit project generator covers this device."}
    leds, btns, uart, an = [], [], None, []
    for b in r["bindings"]:
        for port, pin in b["pins"].items():
            name = pin.get("port_name")
            if not name:
                continue
            if b["type"] in ("led", "relay_module", "buzzer_active") and pin.get("role") == "out":
                leds.append(name)
            elif b["type"] in ("button", "switch") and pin.get("role") == "in":
                btns.append(name)
            elif pin.get("role") == "an" and pin.get("an") is not None:
                an.append(int(pin["an"]))
        if b.get("uart") == 1 and uart is None:
            pins = b["pins"]
            tx = next((p["rp"] for p in pins.values() if p.get("fn") == "U1TX"), None)
            rx = next((p["rp"] for p in pins.values() if p.get("fn") == "U1RX"), None)
            if tx is not None and rx is not None:
                uart = (tx, rx, int(b["params"].get("baud", 115200)))
    start = [{"type": "cl_pin_mode", "fields": {"PIN": p, "MODE": "OUTPUT"}} for p in leds]
    if uart:
        start.append({"type": "cl_uart_begin", "fields": {"BAUD": uart[2], "TX": uart[0], "RX": uart[1]}})
        start.append({"type": "cl_uart_println", "fields": {"TEXT": "ChipLlama circuit ready"}})
    top = []
    if start:
        top.append({"type": "cl_on_start", "inputs": {"DO": {"block": _chain(start)}}, "x": 20, "y": 20})
    if btns and leds:
        top.append({"type": "cl_on_pin_change", "fields": {"PIN": btns[0], "PULLUP": "TRUE"}, "inputs": {"DO": {"block": {
            "type": "controls_if", "inputs": {
                "IF0": {"block": {"type": "logic_negate", "inputs": {"BOOL": {"block": {"type": "cl_pin_read", "fields": {"PIN": btns[0]}}}}}},
                "DO0": {"block": {"type": "cl_pin_toggle", "fields": {"PIN": leds[0]}}}}}}}, "x": 380, "y": 20})
    loop = []
    if an and uart:
        loop += [{"type": "cl_uart_print_num", "fields": {"NL": "TRUE"}, "inputs": {"VALUE": {"block": {"type": "cl_adc_read", "fields": {"CH": an[0]}}}}},
                 {"type": "cl_delay_ms", "fields": {"MS": 200}}]
    elif leds and not btns:
        loop += [{"type": "cl_pin_toggle", "fields": {"PIN": leds[0]}}, {"type": "cl_delay_ms", "fields": {"MS": 500}}]
    if loop:
        top.append({"type": "cl_forever", "inputs": {"DO": {"block": _chain(loop)}}, "x": 20, "y": 260})
    return {"ok": True, "workspace": {"blocks": {"languageVersion": 0, "blocks": top}, "variables": [],
                                      "title": f"From circuit {d.get('name', '')}".strip()}}
