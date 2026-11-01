"""M7.1 - KiCad-like schematic geometry: on-grid, orthogonal, no wire through a body/text, geometric netlist == schema netlist."""
import json, pathlib, pytest
from chipllama import geometry as GM, schematic as sch, circuit as C

EX = sorted((pathlib.Path(__file__).parent.parent / "examples" / "circuit").glob("*.json"))


@pytest.mark.parametrize("p", EX, ids=lambda p: p.stem)
def test_example_geometry_clean(p):
    d = sch.migrate(json.loads(p.read_text()))
    a = GM.audit(d)
    assert a["problems"] == [], a["problems"][:5]
    assert not a.get("dangling")
    G = GM.Geo(d)
    for w in d["wires"]:
        assert w.get("pts") is not None, "unrouted wire in example"
        pts = GM.wire_path(G, w)
        for q in pts:
            assert q[0] % GM.GRID == 0 and q[1] % GM.GRID == 0
        for s, t in zip(pts, pts[1:]):
            assert s[0] == t[0] or s[1] == t[1], "diagonal segment"


def test_tidy_is_idempotent_and_clean():
    d = sch.migrate(json.loads([e for e in EX if e.stem == "autowired"][0].read_text()))
    a = GM.audit(GM.tidy(GM.tidy(d)))
    assert a["problems"] == [] and not a.get("dangling")


def test_route_endpoint_and_meta_geo():
    from fastapi.testclient import TestClient
    from chipllama.server import create_app
    c = TestClient(create_app())
    d = json.loads((EX[0]).read_text())
    for w in d["wires"]:
        w.pop("pts", None)
    r = c.post("/api/circuit/route", json={"circuit": d, "part": ""}).json()
    assert all(w.get("pts") is not None for w in r["circuit"]["wires"])
    assert r["audit"]["problems"] == []
    m = c.get("/api/circuit/meta", params={"device": d.get("device", "PIC16F18877")}).json()
    assert m["geo"]["grid"] == GM.GRID and any(v["kind"] == "res" for v in m["geo"]["sym"].values())


def test_dangling_wire_end_is_reported():
    d = sch.migrate(json.loads(EX[0].read_text()))
    d["parts"].append({"id": "JN99", "type": "junction", "x": 2000, "y": 2000})
    d["wires"].append({"a": d["wires"][0]["a"], "b": "JN99.1", "pts": []})
    codes = {f["code"] for f in C.check(d)["findings"]}
    assert "WIRE_DANGLING" in codes
