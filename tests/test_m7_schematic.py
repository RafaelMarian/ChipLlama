"""M7: KiCad-style schematic - physical pins, shorts, labels, power join, netlist, autowire, migrate."""
import copy
import json
from pathlib import Path

import pytest

from chipllama import circuit, projgen, schematic as SCH, symbols as S

EX = Path(__file__).resolve().parent.parent / "examples" / "circuit"
NAMES = sorted(p.stem for p in EX.glob("*.json"))


def ex(n):
    return json.loads((EX / f"{n}.json").read_text())


def net_with(d, ref, pin):
    return next(n for n in SCH.nets(d) if any(x["ref"] == ref and x["pin"] == pin for x in n["nodes"]))


def test_real_pin_counts():
    assert len(S.entry("button")["pins"]) == 4
    assert {p["num"] for p in S.entry("led")["pins"]} == {"1", "2"} or len(S.entry("led")["pins"]) == 2
    assert len(S.entry("pot")["pins"]) == 3


def test_button_shorts_collapse():
    d = ex("led_button")
    assert d["schema"] == 2
    btn = next(p for p in d["parts"] if p["type"] == "button")
    # pins 1/2 and 3/4 of a tactile switch are the same contact
    n1 = net_with(d, btn["id"], "1")
    assert any(x["ref"] == btn["id"] and x["pin"] == "2" for x in n1["nodes"])


def test_power_symbols_join_by_name():
    d = ex("led_button")
    gnd = [n for n in SCH.nets(d) if n["name"] == "GND"]
    assert len(gnd) == 1 and len(gnd[0]["nodes"]) >= 3


def test_labels_join():
    d = {"schema": 2, "device": "PIC24FJ256GB110", "parts": [
        {"id": "U1", "type": "mcu", "x": 0, "y": 0},
        {"id": "R1", "type": "resistor", "x": 0, "y": 0, "params": {}},
        {"id": "LBL1", "type": "label", "x": 0, "y": 0, "params": {"name": "SIG"}},
        {"id": "LBL2", "type": "label", "x": 0, "y": 0, "params": {"name": "SIG"}}],
        "wires": [{"a": "U1.17", "b": "LBL1.1"}, {"a": "R1.1", "b": "LBL2.1"}]}
    n = net_with(d, "R1", "1")
    assert n["name"] == "SIG" and any(x["ref"] == "U1" and x["pin"] == "17" for x in n["nodes"])


def test_kicad_netlist_format():
    t = SCH.kicad_net(ex("gb110_multi"))
    assert t.startswith("(export") and "(components" in t and "(nets" in t and "(net (code" in t
    assert t.count("(") == t.count(")")
    j = SCH.netlist_json(ex("gb110_multi"))
    assert j["components"] and j["nets"]


def test_migrate_schema1():
    d = {"device": "PIC24FJ256GB110", "parts": [{"id": "U1", "type": "mcu"}, {"id": "LED1", "type": "led", "params": {}}],
         "wires": [{"a": "U1.17", "b": "LED1.A"}]}
    m = SCH.migrate(d)
    assert m["schema"] == 2 and m["wires"][0]["b"].split(".")[1].isdigit()


def test_autowire_led_then_erc_ok():
    d = {"schema": 2, "name": "aw", "device": "PIC24FJ256GB110",
         "parts": [{"id": "U1", "type": "mcu", "x": 420, "y": 60}, {"id": "LED1", "type": "led", "x": 100, "y": 100, "params": {}}], "wires": []}
    r = SCH.autowire(d, "U1")
    assert r["ok"]
    r = SCH.autowire(r["circuit"], "LED1")
    assert r["ok"] and r["circuit"]["wires"]
    assert all(not p["id"].startswith("#") for p in r["circuit"]["parts"])
    assert circuit.check(r["circuit"])["ok"]
    assert all(c["done"] for c in SCH.mcu_checklist(r["circuit"]))


@pytest.mark.parametrize("n", NAMES)
def test_examples_verified(n):
    d = ex(n)
    assert circuit.check(d)["ok"]
    res = projgen.build(d)
    assert res["erc_ok"] and res["verified"]


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from chipllama.server import create_app
    from chipllama.config import Settings
    return TestClient(create_app(Settings()))


def test_endpoints(client):
    d = ex("led_button")
    r = client.post("/api/circuit/netlist", json={"circuit": d, "fmt": "json"})
    assert r.status_code == 200 and r.json()["nets"]
    r = client.post("/api/circuit/netlist", json={"circuit": d})
    assert r.status_code == 200 and r.text.startswith("(export")
    r = client.post("/api/circuit/checklist", json={"circuit": d})
    assert r.status_code == 200 and isinstance(r.json(), list)
    r = client.post("/api/circuit/autowire", json={"circuit": d, "part": "U1"})
    assert r.status_code == 200 and "ok" in r.json()
    old = copy.deepcopy(d); old.pop("schema", None)
    r = client.post("/api/circuit/migrate", json={"circuit": old})
    assert r.status_code == 200 and r.json()["schema"] == 2
