from fastapi.testclient import TestClient

from chipllama import pins, power, explain
from chipllama.cli import main
from chipllama.server import create_app


def test_pins_ok_and_code():
    r = pins.plan([{"fn": "U1TX", "pin": "RP10"}, {"fn": "U1RX", "pin": "RF5"}])
    assert r["ok"] and "RPOR5bits.RP10R = 3;" in r["code"] and "RPINR18bits.U1RXR = 17;" in r["code"]
    assert "__builtin_write_OSCCONL(OSCCON & 0xBF)" in r["code"]


def test_pins_conflicts():
    r = pins.plan([{"fn": "U1TX", "pin": "RF4"}, {"fn": "GPIO_OUT", "pin": "RF4"}, {"fn": "OC1", "pin": "RC1"},
                   {"fn": "U2RX", "pin": "RA2"}, {"fn": "SCL1", "pin": "RB2"}, {"fn": "XYZ", "pin": "RB1"}])
    msgs = " | ".join(i["msg"] for i in r["issues"])
    assert not r["ok"]
    assert "both U1TX and GPIO_OUT" in msgs and "input-only" in msgs and "no RPn" in msgs
    assert "not remappable" in msgs and "unknown function" in msgs


def test_pins_ics_jtag_and_digital():
    r = pins.plan([{"fn": "GPIO_OUT", "pin": "RB6"}, {"fn": "GPIO_IN", "pin": "RA0"}], ics="PGx2", jtag=True)
    sev = [i["severity"] for i in r["issues"]]
    assert r["ok"] and sev.count("warning") == 2
    assert "AD1PCFGL |= 0x0040" in r["code"]


def test_power_estimate():
    run = power.estimate(16e6, ["uart"])
    low = power.estimate(16e6, ["uart"], duty=0.01, battery_mah=1000)
    assert run["avg_ma"] > low["avg_ma"] > low["sleep_ma"]
    assert low["battery_hours"] > 1000 / run["avg_ma"]


def test_explain_code():
    code = "#pragma config FWDTEN = OFF\nT1CON = 0x8030;\nIFS0bits.T1IF = 0;\nvoid __attribute__((interrupt)) _T1Interrupt(void){}\n"
    a = explain.explain_code(code)
    whats = [n["what"] for l in a["lines"] for n in l["notes"]]
    assert any("TON=1" in w and "TCKPS=3" in w for w in whats)
    assert any("IFS0.T1IF" in w for w in whats) and any("_T1Interrupt" in w for w in whats)
    assert {"name": "T1CON", "page": 162} in a["registers"]


def test_cli_pins_and_explain(tmp_path, capsys):
    assert main(["pins", "U1TX=RP10"]) == 0
    assert main(["pins", "OC1=RC1"]) == 1
    f = tmp_path / "m.c"; f.write_text("T1CON = 0x8000;\n")
    assert main(["explain", str(f)]) == 0
    assert "T1CON" in capsys.readouterr().out


def test_api_pins_and_explain():
    c = TestClient(create_app())
    assert any(f["fn"] == "U1TX" for f in c.get("/api/pins/functions").json()["functions"])
    j = c.post("/api/pins/plan", json={"items": [{"fn": "U1TX", "pin": "RP10"}], "power": {"battery_mah": 2000}}).json()
    assert j["ok"] and j["power"]["peripherals"] == ["uart"] and j["power"]["battery_hours"]
    e = c.post("/api/explain", json={"code": "T1CON = 0x8000;"}).json()
    assert e["lines"][0]["notes"][0]["page"] == 162 and e["explanation"] is None
    assert c.post("/api/explain", json={"code": ""}).status_code == 422
