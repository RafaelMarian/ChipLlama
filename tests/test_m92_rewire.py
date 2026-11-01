"""M9.2 Auto re-wire: every device x every example circuit -> 0 drawing problems, no new ERC errors, codegen works."""
import copy
import glob
import json
import os
from multiprocessing import get_context
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chipllama import families as FAM, pinout as PO, projgen
from chipllama.circuit import check
from chipllama.rewire import rewire

ROOT = Path(__file__).resolve().parents[1] / "examples" / "circuit"
EXAMPLES = sorted(glob.glob(str(ROOT / "*.json"))) + sorted(glob.glob(str(ROOT / "families" / "*.json")))
DEVICES = PO.bundled() + [d["id"] for d in FAM.device_list()]


def _errors(res):
    return [f["message"] for f in res["findings"] if f.get("severity") == "error"]


def _job(a):
    f, dev = a
    d = json.load(open(f))
    r = rewire(d, "", dev)
    c = r["circuit"]
    out = {"ex": Path(f).name, "dev": dev, "problems": r["audit"]["problems"][:3], "unrouted": c.get("unrouted"),
           "dangling": r["audit"]["dangling"][:3], "base": len(_errors(check(d, ""))), "errors": _errors(check(c, "")),
           "device": c.get("device"), "summary": r["summary"]}
    try:
        g = projgen.build(c)
        out["gen"] = bool(g.get("files")) and bool(g.get("erc_ok")) and bool(g.get("verified"))
    except Exception as e:      # pragma: no cover
        out["gen"] = repr(e)
    return out


@pytest.fixture(scope="module")
def matrix():
    jobs = [(f, d) for f in EXAMPLES for d in DEVICES]
    with get_context("fork" if os.name == "posix" else "spawn").Pool(min(8, os.cpu_count() or 2)) as p:
        return p.map(_job, jobs, chunksize=4)


def test_matrix_size(matrix):
    assert len(EXAMPLES) >= 50 and len(DEVICES) >= 15 and len(matrix) == len(EXAMPLES) * len(DEVICES)


def test_matrix_zero_overlaps(matrix):
    bad = [(m["ex"], m["dev"], m["problems"], m["unrouted"], m["dangling"]) for m in matrix
           if m["problems"] or m["unrouted"] or m["dangling"]]
    assert not bad, bad[:10]


def test_matrix_no_new_erc_errors(matrix):
    bad = [(m["ex"], m["dev"], m["errors"][:2]) for m in matrix if len(m["errors"]) > m["base"]]
    assert not bad, bad[:10]


def test_matrix_codegen(matrix):
    bad = [(m["ex"], m["dev"], m["gen"]) for m in matrix if m["gen"] is not True]
    assert not bad, bad[:10]


def test_matrix_device_set(matrix):
    for m in matrix:
        fam = FAM.board(m["dev"])
        assert (m["device"] or "").upper() == (fam["id"] if fam else m["dev"]), m["dev"]
        assert m["summary"]["overlaps"] == 0


def _ex(n):
    p = ROOT / f"{n}.json"
    return json.load(open(p if p.exists() else ROOT / "families" / f"{n}.json"))


def test_pure_and_keeps_valid_pins():
    d = _ex("led_button")
    before = copy.deepcopy(d)
    r = rewire(d, "")
    assert d == before                                     # input untouched (one undo step on the client)
    assert r["summary"]["overlaps"] == 0 and not r["summary"]["dropped"]
    mcu_eps = lambda c: {e for w in c["wires"] for e in (w["a"], w["b"]) if e.startswith("U1.")}
    r2 = rewire(r["circuit"], "")                          # idempotent pin choice on the same device
    assert not r2["summary"]["remapped"]
    assert mcu_eps(r2["circuit"]) == mcu_eps(r["circuit"])


def test_board_drops_bare_mcu_support_and_pic_gets_it_back():
    r = rewire(_ex("led_button"), "", "ARDUINO-MEGA2560")
    c = r["circuit"]
    assert c["power"] == "board" and c.get("family") == "avr"
    assert not any(p["type"] == "icsp" for p in c["parts"]) and r["summary"]["removed_support"]
    back = rewire(c, "", "PIC24FJ256GB110")["circuit"]
    assert any(p["type"] == "icsp" for p in back["parts"]) and "family" not in back
    assert check(back, "")["ok"]


def test_uart_pins_share_a_module():
    r = rewire(_ex("arduino_uno_uart"), "", "PIC32MX795F512L")
    assert not _errors(r["check"])
    fns = set(r["circuit"]["assign"].values())
    mods = {f[1] for f in fns if f[0] == "U"}
    assert len(mods) == 1, fns


def test_5v_adapter_retargeted_to_3v3():
    r = rewire(_ex("arduino_uno_uart"), "", "ESP32-DEVKITC")
    assert not _errors(r["check"])
    assert r["summary"]["adjusted"] == ["USB1 I/O level set to 3.3 V"]


def test_endpoint_and_fix_route():
    from chipllama.config import Settings
    from chipllama.server import create_app
    c = TestClient(create_app(Settings()))
    j = c.post("/api/circuit/rewire", json={"circuit": _ex("led_button"), "part": "STM32-BLUEPILL"}).json()
    assert j["summary"]["device"] == "STM32-BLUEPILL" and j["summary"]["overlaps"] == 0
    bad = copy.deepcopy(j["circuit"])
    w = next(w for w in bad["wires"] if w.get("pts") is not None and "U1." in w["a"] + w["b"])
    u = next(p for p in bad["parts"] if p["id"] == "U1")
    w["pts"] = [{"x": u["x"] + 60, "y": u["y"] - 40}, {"x": u["x"] + 60, "y": u["y"] + 400}]
    a = c.post("/api/circuit/route", json={"circuit": bad, "part": "check"}).json()["audit"]
    assert any("crosses U1" in p for p in a["problems"])
    f = c.post("/api/circuit/route", json={"circuit": bad, "part": "fix"}).json()
    assert not f["audit"]["problems"]


def test_ui_has_button_hotkey_badge():
    web = Path(__file__).resolve().parents[1] / "chipllama" / "web"
    html, js = (web / "index.html").read_text(encoding="utf-8"), (web / "circuit.js").read_text(encoding="utf-8")
    assert 'id="crewire"' in html and "Shift+R" in html and 'id="cgeo"' in html
    assert "k==='r'&&e.shiftKey" in js and "/api/circuit/rewire" in js and "Re-wired for" in js
