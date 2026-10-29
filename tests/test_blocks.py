"""Blocks tab: deterministic C generator, verification and API."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chipllama import blocks
from chipllama.config import Settings
from chipllama.server import create_app

EX = Path(__file__).resolve().parent.parent / "examples" / "blocks"
NAMES = ["adc_logger", "blink", "button_led", "spi_i2c", "uart_hello"]


def ws(*tops, vars=()):
    return {"blocks": {"blocks": list(tops)}, "variables": [{"name": v, "id": "v_" + v} for v in vars]}


def B(t, fields=None, inputs=None, nxt=None):
    b = {"type": t, "fields": fields or {}}
    if inputs:
        b["inputs"] = {k: {"block": v} for k, v in inputs.items()}
    if nxt:
        b["next"] = {"block": nxt}
    return b


def gen(w):
    return blocks.generate(w, Settings())


def test_five_examples_exist():
    assert sorted(p.stem for p in EX.glob("*.json")) == NAMES


@pytest.mark.parametrize("name", NAMES)
def test_examples_verified(name):
    r = gen(json.loads((EX / f"{name}.json").read_text()))
    assert r["error"] == ""
    assert r["verified"], r["findings"]
    assert "int main(void)" in r["c"] and "#pragma config" in r["c"]
    assert not [f for f in r["findings"] if f["severity"] == "error"]


def test_deterministic():
    w = json.loads((EX / "uart_hello.json").read_text())
    assert gen(w)["c"] == gen(w)["c"]


def test_blink_timer_values():
    c = gen(json.loads((EX / "blink.json").read_text()))["c"]
    assert "PR1 = 31249" in c and "_T1Interrupt" in c and "LATAbits.LATA0 ^= 1;" in c
    assert re.search(r"TCKPS\s*=\s*3", c)
    assert "IFS0bits.T1IF = 0" in c


def test_uart_baud_and_pps():
    c = gen(json.loads((EX / "uart_hello.json").read_text()))["c"]
    assert "U1BRG = 34" in c and "BRGH = 1" in c
    assert "RPOR5bits.RP10R = 3" in c and "RPINR18bits.U1RXR = 17" in c
    assert 'uart_print("hello from ChipLlama\\r\\n");' in c


def test_button_change_notification():
    c = gen(json.loads((EX / "button_led.json").read_text()))["c"]
    assert "_CNInterrupt" in c and "CNPU" in c and "TRISDbits.TRISD6 = 1" in c
    assert "(!PORTDbits.RD6)" in c


def test_adc_pcfg_and_variable_volatile():
    c = gen(json.loads((EX / "adc_logger.json").read_text()))["c"]
    assert "AD1PCFGL = 0xFFDF" in c and "adc_read(5)" in c
    assert "volatile int level;" in c


def test_spi_i2c():
    r = gen(json.loads((EX / "spi_i2c.json").read_text()))
    assert "SPI1" in r["intent"] and "I2C1" in r["intent"]
    assert "spi1_xfer(170)" in r["c"] and "i2c1_write(0x48, 1)" in r["c"]


def test_empty_workspace_is_an_error():
    r = gen({"blocks": {"blocks": []}})
    assert r["error"] and not r["verified"]


def test_bad_i2c_address():
    r = gen(ws(B("cl_on_start", inputs={"DO": B("cl_i2c_write", {"ADDR": "0x99"}, {"VALUE": B("math_number", {"NUM": 1})})})))
    assert "7-bit" in r["error"]


def test_bad_adc_channel():
    r = gen(ws(B("cl_forever", inputs={"DO": B("cl_uart_print_num", {}, {"VALUE": B("cl_adc_read", {"CH": 40})})})))
    assert "AN0..AN15" in r["error"]


def test_pin_change_requires_cn_pin():
    cn = {p["pin"] for p in blocks.pin_meta() if p["cn"]}
    assert "RD6" in cn


def test_defaults_warn_when_begin_missing():
    r = gen(ws(B("cl_forever", inputs={"DO": B("cl_uart_println", {"TEXT": "x"})})))
    assert r["verified"] and any("UART begin" in w for w in r["warnings"])


def test_string_escaping():
    r = gen(ws(B("cl_on_start", inputs={"DO": B("cl_uart_println", {"TEXT": 'say "hi" \\ ok'})})))
    assert r["verified"]
    assert 'uart_print("say \\"hi\\" \\\\ ok\\r\\n");' in r["c"]


def test_control_and_math():
    loop = B("controls_repeat_ext", inputs={"TIMES": B("math_number", {"NUM": 3}),
                                            "DO": B("cl_pin_toggle", {"PIN": "RA1"})})
    w = ws(B("cl_on_start", inputs={"DO": B("variables_set", {"VAR": {"id": "v_n"}}, {
        "VALUE": B("math_arithmetic", {"OP": "MULTIPLY"}, {"A": B("math_number", {"NUM": 2}), "B": B("math_number", {"NUM": 5})})}, nxt=loop)}),
        vars=["n"])
    r = gen(w)
    assert r["verified"], r
    assert "n = (2 * 5);" in r["c"] or "n = 2 * 5;" in r["c"]
    assert "for (" in r["c"]


def test_api_meta_example_generate():
    c = TestClient(create_app(Settings()))
    m = c.get("/api/blocks/meta").json()
    assert len(m["examples"]) == 5 and any(p["pin"] == "RA0" for p in m["pins"])
    w = c.get("/api/blocks/example/blink").json()
    r = c.post("/api/blocks/generate", json={"workspace": w}).json()
    assert r["verified"]
    assert c.get("/api/blocks/example/..%2Fsecret").status_code == 404


def test_vendor_blockly_served_with_notice():
    c = TestClient(create_app(Settings()))
    r = c.get("/vendor/blockly.min.js")
    assert r.status_code == 200 and len(r.content) > 500_000
    assert "Apache" in c.get("/vendor/NOTICE.md").text
    assert c.get("/static/blocks.js").status_code == 200
    assert c.get("/static/../server.py").status_code == 404


def test_index_has_blocks_tab():
    html = (Path(blocks.__file__).parent / "web" / "index.html").read_text()
    assert "/vendor/blockly.min.js" in html and "Polish with Muse" in html
