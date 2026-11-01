import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from chipllama import circuit, projgen, compiler
from chipllama.config import Settings
from chipllama.projgen import bridge
from chipllama.projgen.syntax import syntax_check

EX = Path(__file__).resolve().parent.parent / "examples" / "circuit"
NAMES = sorted(p.stem for p in EX.glob("*.json"))


def ex(n):
    return json.loads((EX / f"{n}.json").read_text())


def test_examples_present():
    assert {"led_button", "servo_pot", "i2c_logger", "gb110_multi", "k22_logger"} <= set(NAMES)


@pytest.mark.parametrize("n", NAMES)
def test_example_erc_verified_syntax(n):
    d = ex(n)
    assert circuit.check(d)["ok"]
    res = projgen.build(d)
    assert res["erc_ok"] and res["verified"], res["check"].get("findings")
    for top in ("config/", "hal/", "drivers/", "board/", "app/main.c", "Makefile"):
        assert any(f.startswith(top) for f in res["files"]), top
    assert any(f.startswith("nbproject/") for f in res["files"])
    sc = syntax_check(res, projgen.load_dev(res["device"]))
    assert sc["status"] in ("ok", "skipped"), sc["output"][:500]


def test_bad_endpoint():
    d = ex("led_button")
    d["wires"].append({"a": "U1.9999", "b": "gnd.-"})
    d["wires"].append({"a": "NOPE.1", "b": "gnd.-"})
    r = circuit.check(d)
    assert not r["ok"]
    assert sum(f["code"] == "BAD_ENDPOINT" for f in r["findings"]) >= 2


def test_meta_gb110():
    m = circuit.meta("PIC24FJ256GB110")
    assert m["device"]["count"] == 100 and m["device"]["layout"] == "quad"
    p = {x["num"]: x for x in m["device"]["pins"]}
    assert any("RP" in fn or "U1TX" in fn for x in p.values() for fn in x["functions"])
    assert {l["type"] for l in m["library"]} >= {"led", "button", "pot", "servo", "tmp102"}


@pytest.mark.parametrize("drv,n", [("led", "led_button"), ("button", "led_button"), ("servo", "servo_pot"),
                                   ("pot", "servo_pot"), ("tmp102", "i2c_logger"), ("uart", "i2c_logger")])
def test_per_driver_files(drv, n):
    res = projgen.build(ex(n))
    names = " ".join(res["files"])
    assert drv in names.lower(), (drv, list(res["files"]))


def test_bridge_to_blocks():
    r = bridge.to_blocks(ex("led_button"))
    assert r["ok"] and r["workspace"]["blocks"]["blocks"]


def test_compile_project_xc16_fake(monkeypatch, tmp_path):
    res = projgen.build(ex("led_button"))
    monkeypatch.setattr(compiler, "find_compiler", lambda s, dev=None: ("xc16", "/x/xc16-gcc"))
    monkeypatch.setattr(compiler, "find_tool", lambda *a, **k: "/x/xc16-bin2hex")
    cmds = []

    def runner(cmd, **kw):
        cmds.append(cmd)
        if "bin2hex" in cmd[0]:
            (tmp_path / "app.hex").write_text(":00000001FF\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    r = compiler.compile_project(res, Settings(), runner=runner, workdir=str(tmp_path))
    nsrc = sum(f.endswith(".c") for f in res["files"])
    assert sum("-c" in c for c in cmds) == nsrc
    assert r.status == "ok" and r.hex_path


def test_compile_project_xc8_fake(monkeypatch, tmp_path):
    res = projgen.build(ex("k22_logger"))
    monkeypatch.setattr(compiler, "find_compiler", lambda s, dev=None: ("xc8", "/x/xc8-cc"))

    def runner(cmd, **kw):
        (tmp_path / "app.hex").write_text(":00000001FF\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    r = compiler.compile_project(res, Settings(), runner=runner, workdir=str(tmp_path))
    assert r.status == "ok" and r.hex_path


def test_server_endpoints():
    from chipllama.server import create_app
    c = TestClient(create_app(Settings()))
    assert c.get("/api/circuit/meta", params={"device": "PIC18F45K22"}).status_code == 200
    exs = c.get("/api/circuit/examples").json()
    assert len(exs) >= 5
    d = c.get("/api/circuit/example/led_button").json()
    assert c.post("/api/circuit/check", json={"circuit": d}).json()["ok"]
    g = c.post("/api/circuit/generate", json={"circuit": d}).json()
    assert g["verified"]
    z = c.post("/api/circuit/project.zip", json={"circuit": d})
    assert z.status_code == 200 and z.content[:2] == b"PK"
    d["wires"].append({"a": "U1.9999", "b": "gnd.-"})
    assert c.post("/api/circuit/project.zip", json={"circuit": d}).status_code == 400
    assert c.post("/api/circuit/to_blocks", json={"circuit": ex("led_button")}).status_code == 200
