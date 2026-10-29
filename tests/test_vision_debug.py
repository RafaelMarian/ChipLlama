import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chipllama import cli
from chipllama.models import ModelError
from chipllama.server import create_app
from chipllama.vision_debug import (Extraction, _loose_json, analyze, code_registers, extract, fix_diff,
                                    format_report, normalize, parse_intent, vision_debug)
from conftest import GOOD_T1, ScriptedModel, compile_ok, fence

EX = Path(__file__).resolve().parent.parent / "examples" / "debug"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
DEMOS = ["sfr_timer1_prescaler_bug", "sfr_pll_off", "scope_20ms_instead_of_10ms"]


def demo(name):
    j = json.loads((EX / f"{name}.extraction.json").read_text())
    return normalize(j), (EX / f"{name}.intent.txt").read_text().strip()


# ---------------------------------------------------------------- JSON repair
@pytest.mark.parametrize("txt,fix", [
    ('```json\n{"kind": "registers", "registers": {"T1CON": "0x8000"}}\n```', "fence"),
    ('Sure! {"kind": "registers", "registers": {"T1CON": "0x8000",},} hope it helps', "around"),
    ("{'kind': 'registers', 'registers': {'T1CON': '0x8000'}}", "syntax"),
    ('{kind: "registers", registers: {T1CON: 0x8000}}', "syntax"),
    ('{"kind": "registers", "registers": {"T1CON": "0x8000"', "truncated"),
    ('{"kind": "registers", "registers": {"T1CON": "0x8000"}, "confidence": None}', "syntax"),
])
def test_loose_json_repairs(txt, fix):
    fixes = []
    ex = normalize(_loose_json(txt, fixes), fixes)
    assert ex.registers["T1CON"] == 0x8000
    assert any(fix in f for f in fixes)


def test_loose_json_rejects_garbage():
    with pytest.raises(ValueError):
        _loose_json("I cannot read this image", [])


def test_normalize_list_units_duty_and_unknown_regs():
    ex = normalize({"registers": [{"name": "pr1", "value": "4E1Fh"}, {"name": "FOO", "value": "0x1"}],
                    "scope": {"freq_hz": "50 Hz", "duty": 50}, "confidence": 97})
    assert ex.registers == {"PR1": 0x4E1F} and ex.kind == "registers"
    assert abs(ex.scope["period_ms"] - 20) < 1e-9 and ex.scope["duty"] == 0.5 and ex.confidence == 0.97
    assert any("dropped FOO" in f for f in ex.repaired)
    sc = normalize({"scope": {"period_ms": "20ms"}})
    assert sc.kind == "scope" and sc.scope["freq_hz"] == 50


def test_extract_reasks_once_then_fails():
    m = ScriptedModel(["no json here", '{"kind":"registers","registers":{"T1CON":"0x8010"},"confidence":0.9}'])
    ex = extract(m, [PNG], "timer 10 ms")
    assert ex.registers["T1CON"] == 0x8010 and "re-asked" in ex.repaired[0]
    assert m.calls[0][0].images == [PNG] and len(m.calls[1]) == 3
    with pytest.raises(ModelError):
        extract(ScriptedModel(["nope", "still nope"]), [PNG])


# ---------------------------------------------------------------- intent / code
def test_parse_intent():
    t = parse_intent("Timer1 interrupt every 10 ms, toggle RA0")
    assert abs(t.period_s - 0.010) < 1e-12 and t.toggle and abs(t.wave_period_s - 0.020) < 1e-12
    s = parse_intent("RA0 square wave with 10 ms period (100 Hz)")
    assert not s.toggle and abs(s.wave_period_s - 0.010) < 1e-12
    u = parse_intent("UART1 115200 baud")
    assert u.baud == 115200 and u.period_s is None


def test_code_registers_evaluates_defines():
    regs, cfg = code_registers(GOOD_T1)
    assert regs["PR1"] == 19999 and cfg["FNOSC"] == "FRCPLL"


# ---------------------------------------------------------------- the three demos
def test_demo_prescaler_bug():
    ex, it = demo("sfr_timer1_prescaler_bug")
    rep = vision_debug([], it, extraction=ex)
    st = {r.register: r.status for r in rep.analysis.rows}
    assert st["T1CON"] == "bad" and st["OSCCON"] == "ok" and st["Timer1 period"] == "bad"
    assert "TCKPS" in rep.root_cause and rep.verdict.startswith("BUG FOUND")
    assert "T1CONbits.TCKPS = 1;" in rep.fix_diff and "TCKPS" in rep.regenerate_context


def test_demo_pll_off():
    ex, it = demo("sfr_pll_off")
    rep = vision_debug([], it, extraction=ex)
    st = {r.register: r.status for r in rep.analysis.rows}
    assert st["OSCCON"] == "bad" and st["T1CON"] == "ok"
    assert "FRCPLL" in rep.fix_diff and "40 ms" in format_report(rep)


def test_demo_scope_with_code_gives_real_diff():
    ex, it = demo("scope_20ms_instead_of_10ms")
    code = (EX / "toggle_10ms.c").read_text()
    rep = vision_debug([], it, code=code, extraction=ex)
    assert "toggles the pin" in rep.root_cause
    assert "+    PR1 = 9999;" in rep.fix_diff and rep.fix_diff.startswith("--- a/main.c")


@pytest.mark.parametrize("name", DEMOS)
def test_expected_outputs_match(name, capsys):
    a = ["debug", "--image", str(EX / f"{name}.png"), "--extraction", str(EX / f"{name}.extraction.json"),
         "--intent", (EX / f"{name}.intent.txt").read_text().strip(), "--no-model"]
    if name.startswith("scope"):
        a += ["--code", str(EX / "toggle_10ms.c")]
    assert cli.main(a) == 0
    out = capsys.readouterr().out
    exp = (EX / f"{name}.expected.txt").read_text()
    assert out.strip().splitlines()[-1] == exp.strip().splitlines()[-1]
    assert out.strip().splitlines()[-1].startswith("VERDICT: BUG FOUND")


def test_matching_image_is_ok():
    ex = normalize({"kind": "registers", "registers": {"OSCCON": "0x1120", "CLKDIV": "0x0100", "T1CON": "0x8010",
                                                         "PR1": "0x4E1F"}, "confidence": 0.9})
    rep = vision_debug([], "Timer1 interrupt every 10 ms", extraction=ex)
    assert rep.verdict.startswith("OK") and not rep.fix_diff


def test_brgh_mismatch_and_serial_garbage():
    # BRG 34 is the BRGH=1 value for 115200 @ 16 MHz, but BRGH=0
    ex = normalize({"kind": "registers", "registers": {"OSCCON": "0x1120", "CLKDIV": "0x0100", "U1MODE": "0x8000",
                                                         "U1STA": "0x0400", "U1BRG": "0x0022"}})
    rep = vision_debug([], "UART1 115200 baud", extraction=ex)
    assert any("BRGH mismatch" in c for c in rep.analysis.causes)
    assert "U1MODEbits.BRGH = 1;" in rep.fix_diff
    g = normalize({"kind": "serial", "serial": {"text": "H\ufffd\ufffdl\u00ff\u00fe\ufffdo \ufffd\ufffd\u00ff\ufffd"}})
    r2 = vision_debug([], "UART1 115200 baud", extraction=g)
    assert r2.analysis.computed["serial_garbage_ratio"] > 0.3 and any("baud" in c for c in r2.analysis.causes)


def test_fix_diff_without_code_is_suggestion():
    ex, it = demo("sfr_timer1_prescaler_bug")
    rep = vision_debug([], it, extraction=ex)
    assert fix_diff("", rep.analysis.fixes).splitlines()[1].startswith("+++ suggested")
    stale = vision_debug([], it, code=GOOD_T1, extraction=ex)
    assert stale.fix_diff and any("DIFFERENT firmware" in n for n in stale.notes)


def test_full_pipeline_with_model_explains():
    m = ScriptedModel([(EX / "sfr_timer1_prescaler_bug.extraction.json").read_text(),
                       "Root cause: TCKPS 1:1 (p.162)\n```diff\n+T1CONbits.TCKPS = 1;\n```"])
    rep = vision_debug([PNG], "Timer1 interrupt every 10 ms, toggle RA0", model=m)
    assert "p.162" in rep.explanation and rep.verdict.startswith("BUG")
    assert "T1CON | 0x8000" in m.calls[1][1].text


# ---------------------------------------------------------------- CLI / server
def test_cli_no_model_without_extraction_errors(capsys):
    assert cli.main(["debug", "--image", str(EX / "sfr_pll_off.png"), "--intent", "x", "--no-model"]) == 2


def test_server_vision_endpoint(settings):
    m = ScriptedModel([(EX / "sfr_pll_off.extraction.json").read_text(), "explained p.123"])
    c = TestClient(create_app(settings, model_factory=lambda b: m))
    r = c.post("/api/debug/vision", data={"intent": "Timer1 interrupt every 10 ms"},
               files=[("images", ("a.png", PNG, "image/png"))])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["verdict"].startswith("BUG") and j["explanation"] == "explained p.123"
    assert any(f["register"] == "OSCCON" and f["status"] == "bad" for f in j["findings"])
    assert c.post("/api/debug/vision", data={"intent": "x"}).status_code == 422
    bad = TestClient(create_app(settings, model_factory=lambda b: ScriptedModel(["x", "y"])))
    assert bad.post("/api/debug/vision", files=[("images", ("a.png", PNG, "image/png"))]).status_code == 502
    off = c.post("/api/debug/vision", data={"extraction": (EX / "sfr_pll_off.extraction.json").read_text(),
                                             "intent": "Timer1 10 ms", "explain": "false"},
                 files=[("images", ("a.png", PNG, "image/png"))])
    assert off.status_code == 200 and off.json()["explanation"] == ""


def test_server_serves_demo_examples(settings):
    c = TestClient(create_app(settings, model_factory=lambda b: ScriptedModel([])))
    assert c.get("/api/examples/debug/sfr_pll_off.png").content[:4] == b"\x89PNG"
    assert "toggle" in c.get("/api/examples/debug/scope_20ms_instead_of_10ms.json").json()["code"]
    assert c.get("/api/examples/debug/..%2Fsecret.png").status_code == 404


def test_generate_context_reaches_prompt(settings, monkeypatch):
    import chipllama.server as srv
    monkeypatch.setattr(srv, "compile_c", compile_ok, raising=False)
    m = ScriptedModel([fence(GOOD_T1)] * 4)
    c = TestClient(create_app(settings, model_factory=lambda b: m))
    r = c.post("/api/generate", json={"request": "Timer1 interrupt every 10 ms at 32 MHz priority 5",
                                      "use_images": False, "context": "REVERSE-DEBUG-MARKER TCKPS"})
    assert r.status_code == 200
    assert "REVERSE-DEBUG-MARKER" in m.calls[0][1].text
