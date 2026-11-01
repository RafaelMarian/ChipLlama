"""M8.1 - KiCad-style layout: every family example and every board's auto-wire output must be geometrically clean."""
import json, pathlib, pytest
from chipllama import geometry as GM, schematic as sch
from chipllama.families.boards import boards

FAM = sorted((pathlib.Path(__file__).parent.parent / "examples" / "circuit" / "families").glob("*.json"))


def _clean(d):
    a = GM.audit(d)
    assert a["problems"] == [], a["problems"][:5]
    assert not a.get("dangling"), a["dangling"]
    assert not d.get("unrouted"), d.get("unrouted")
    for w in d["wires"]:
        assert w.get("pts") is not None, f"unrouted wire {w['a']}-{w['b']}"


@pytest.mark.parametrize("p", FAM, ids=lambda p: p.stem)
def test_family_example_clean(p):
    _clean(sch.migrate(json.loads(p.read_text())))


@pytest.mark.parametrize("p", [f for f in FAM if f.stem.endswith("_i2c")], ids=lambda p: p.stem)
def test_pullups_vertical_with_own_supply(p):
    d = sch.migrate(json.loads(p.read_text()))
    G = GM.Geo(d)
    for r in [q for q in d["parts"] if q["type"] == "resistor"]:
        a, b = G.pin(f"{r['id']}.1"), G.pin(f"{r['id']}.2")
        assert a[0] == b[0], f"{r['id']} pull-up not vertical"
        # each pull-up has its own power symbol
        nb = [w["a"] if w["b"].startswith(r["id"] + ".") else w["b"] for w in d["wires"]
              if r["id"] + "." in (w["a"][:len(r["id"]) + 1], w["b"][:len(r["id"]) + 1])]
        assert any(next(q for q in d["parts"] if q["id"] == e.split(".")[0])["type"] in ("vdd33", "v5", "pwr")
                   for e in nb if "." in e)


@pytest.mark.parametrize("bid", sorted(boards()))
def test_board_autowire_clean(bid):
    d = {"schema": 2, "device": bid, "parts": [{"id": "U1", "type": "mcu", "x": 0, "y": 0, "rot": 0, "params": {}}],
         "wires": []}
    for pid, t, y in (("S1", "tmp102", 40), ("LED1", "led", 260), ("SW1", "button", 420)):
        d["parts"].append({"id": pid, "type": t, "x": 600, "y": y, "rot": 0, "params": {}})
        r = sch.autowire(d, pid)
        assert r["ok"], r
        d = r["circuit"]
        _clean(d)
    _clean(GM.tidy(d))
