import json

from fastapi.testclient import TestClient

from chipllama.config import Settings, settings_path
from chipllama.server import create_app
from conftest import GOOD_T1, ScriptedModel, compile_ok, fence


def client(settings=None, replies=()):
    m = ScriptedModel(list(replies))
    return TestClient(create_app(settings or Settings(meta_api_key="sk-secret-123"),
                                 model_factory=lambda b: m, compile_fn=compile_ok)), m


def test_get_settings_never_echoes_keys():
    c, _ = client()
    r = c.get("/api/settings")
    assert r.status_code == 200
    body = r.text
    assert "sk-secret-123" not in body
    st = r.json()["settings"]
    assert st["meta_api_key_set"] is True and st["baseten_api_key_set"] is False
    assert "meta_api_key" not in st
    assert st["clock_profile"] == "frcpll" and st["ics"] == "PGx2" and st["ipecmd_tool"] == "PK5"
    assert {p["key"] for p in r.json()["options"]["clock_profile"]} == {"frcpll", "frcpll_div2", "frc", "xtal"}


def test_post_settings_persists_to_settings_json_not_env(tmp_path):
    c, _ = client()
    r = c.post("/api/settings", json={"ics": "PGx1", "clock_profile": "frc", "max_iters": 6,
                                      "ipecmd_power_target": True, "baseten_api_key": "bt-new-key"})
    assert r.status_code == 200, r.text
    assert "bt-new-key" not in r.text
    st = r.json()["settings"]
    assert st["ics"] == "PGx1" and st["clock_profile"] == "frc" and st["max_iters"] == 6
    assert st["ipecmd_power_target"] is True and st["baseten_api_key_set"] is True
    saved = json.loads(settings_path().read_text())
    assert saved["ics"] == "PGx1" and saved["baseten_api_key"] == "bt-new-key"
    assert not (settings_path().parent / ".env").exists()
    # live: pragmas endpoint follows the new settings
    p = c.get("/api/pragmas").json()
    assert "ICS = PGx1" in p["text"] and "FNOSC = FRC " in p["text"] and p["fosc_hz"] == 8e6


def test_empty_key_keeps_existing_and_clear_removes():
    c, _ = client(Settings())
    c.post("/api/settings", json={"meta_api_key": "k1"})
    assert c.get("/api/settings").json()["settings"]["meta_api_key_set"]
    c.post("/api/settings", json={"meta_api_key": ""})
    assert json.loads(settings_path().read_text())["meta_api_key"] == "k1"
    c.post("/api/settings", json={"meta_api_key": "__clear__"})
    assert "meta_api_key" not in json.loads(settings_path().read_text())
    assert not c.get("/api/settings").json()["settings"]["meta_api_key_set"]


def test_validation_errors():
    c, _ = client()
    for bad in ({"ics": "PGx9"}, {"clock_profile": "pll"}, {"backend": "openai"}, {"max_iters": 99},
                {"nope": 1}, {"crystal_hz": "abc"}):
        assert c.post("/api/settings", json=bad).status_code == 422, bad
    assert not settings_path().exists()


def test_settings_json_overrides_env_settings(monkeypatch):
    settings_path().write_text(json.dumps({"ics": "PGx3", "meta_model": "m2"}))
    s = Settings.from_env()
    assert s.ics == "PGx3" and s.meta_model == "m2"


def test_doctor_and_detect():
    c, _ = client()
    rows = c.get("/api/doctor").json()["rows"]
    labels = [r["label"] for r in rows]
    assert "xc16-gcc" in labels and "clock profile" in labels
    assert "sk-secret-123" not in json.dumps(rows)
    d = c.post("/api/detect").json()
    assert "xc16_gcc" in d and "ipecmd" in d


def test_stream_emits_steps_and_diff():
    bare = "\n".join(l for l in GOOD_T1.splitlines() if not l.startswith("#pragma"))
    c, _ = client(replies=[fence(bare), fence(GOOD_T1)])
    r = c.post("/api/generate/stream", json={"request": "Timer1 interrupt every 10 ms priority 5", "use_images": False})
    ev = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    kinds = [e["event"] for e in ev]
    assert kinds[0] == "spec" and kinds.count("attempt") == 2 and kinds[-1] == "done"
    atts = [e["data"] for e in ev if e["event"] == "attempt"]
    assert atts[0]["diff"] == "" and "+#pragma config FNOSC = FRCPLL" in atts[1]["diff"]
    assert ev[-1]["data"]["status"] == "verified"


def test_flash_disabled_by_default_and_needs_confirm():
    c, _ = client()
    assert c.post("/api/flash", json={"code": GOOD_T1, "confirm": True}).json()["status"] == "disabled"
    c.post("/api/settings", json={"enable_flash": True})
    r = c.post("/api/flash", json={"code": GOOD_T1}).json()
    assert r["status"] == "disabled" and "confirm" in r["reason"]


def test_index_served():
    c, _ = client()
    html = c.get("/").text
    assert "Settings" in html and "Reverse debug" in html and "Doctor" in html


def test_doctor_levels():
    from chipllama.config import Settings
    from chipllama.doctor import doctor
    s = Settings.from_env()
    s.backend, s.meta_api_key, s.baseten_api_key, s.enable_flash = "meta", "", "", False
    rows = {r["label"]: r for r in doctor(s)}
    assert all(r["level"] in ("ok", "warn", "info", "error") and "ok" in r for r in rows.values())
    assert rows["BASETEN_API_KEY"]["level"] == "info" and "optional" in rows["BASETEN_API_KEY"]["value"]
    assert rows["MODEL_API_KEY"]["level"] == "error" and rows["MODEL_API_KEY"]["ok"] is False
    assert rows["flash"]["level"] == "info"
