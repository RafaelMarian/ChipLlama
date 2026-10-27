import io
import zipfile

from fastapi.testclient import TestClient

from chipllama.debug import analyze, debug_session, decode, parse_dump
from chipllama.server import create_app
from conftest import GOOD_T1, ScriptedModel, compile_ok, fence

DUMP = """T1CON = 0x0030
PR1: 0xFFFF
IEC0 0x0000
IFS0 = 0x0008
IPC0 = 0x4444
LATB = 0x00FF
"""


def test_parse_dump_formats():
    r = parse_dump(DUMP)
    assert r == {"T1CON": 0x30, "PR1": 0xFFFF, "IEC0": 0, "IFS0": 8, "IPC0": 0x4444}   # LATB not modelled
    assert parse_dump("T1CON (0x0104) = 0b1000_0000_0001_0000")["T1CON"] == 0x8010
    assert parse_dump("PR2=270Fh")["PR2"] == 0x270F


def test_decode_fields():
    d = decode({"T1CON": 0x8030})[0]
    assert d["fields"]["TON"] == 1 and d["fields"]["TCKPS"] == 3 and d["page"] == 162
    assert decode({"T1CON": 0xFFFF})[0]["unimplemented_bits_set"] == "0x5F89"


def test_rule_based_findings_for_dead_timer():
    f, info = analyze(parse_dump(DUMP), 32e6, 0.010)
    codes = {x.code for x in f}
    assert {"Timer1_OFF", "Timer1_PR_DEFAULT", "Timer1_IF_SET"} <= codes
    assert any(x.code == "Timer1_MISMATCH" for x in f)
    assert abs(info["timers"]["Timer1"]["period_s"] - 65536 * 256 / 16e6) < 1e-9


def test_priority_zero_and_external_clock_and_stuck_flag():
    f, _ = analyze({"T1CON": 0x8002, "IPC0": 0x0444, "IEC0": 0x8, "IFS0": 8, "PR1": 100})
    c = {x.code for x in f}
    assert {"Timer1_TCS", "Timer1_IP", "Timer1_IF_STUCK"} <= c


def test_t32_mode_uses_timer3_interrupt():
    f, info = analyze({"T2CON": 0x8008, "T3CON": 0x8000, "PR2": 0x0000, "PR3": 0x0001, "IEC0": 0x100, "IPC2": 0x0004})
    assert any(x.code == "T32" for x in f)
    assert "Timer3" not in info["timers"] and info["timers"]["Timer2"]["PR"] == 0x10000


def test_debug_session_with_model_and_screenshot():
    png = b"\x89PNG\r\n\x1a\n" + b"2" * 16
    m = ScriptedModel(['{"T1CON": "0x0030", "PR1": "0x270F", "BOGUS": "0x1"}', "TON is 0 - set T1CONbits.TON = 1."])
    r = debug_session("IEC0 = 0x0008", [png], "timer never fires", model=m)
    assert r.registers["T1CON"] == 0x30 and r.registers["IEC0"] == 8 and "BOGUS" not in r.registers
    assert r.transcribed_from_image == {"T1CON": "0x0030", "PR1": "0x270F"}
    assert "TON" in r.explanation
    assert m.calls[0][0].images == [png]                       # screenshot went to the vision call
    assert "Timer1: TON=0" not in m.calls[1][1].text and "T1CON.TON=0" in m.calls[1][1].text                  # explanation prompt contains rule findings


def test_debug_without_model_is_deterministic():
    r = debug_session(DUMP, model=None)
    assert r.explanation == "" and any(f.severity == "error" for f in r.findings)
    assert debug_session("hello").notes


# ----- FastAPI
def make_client(settings, replies):
    m = ScriptedModel(replies)
    return TestClient(create_app(settings, model_factory=lambda b: m, compile_fn=compile_ok)), m


def test_api_status_and_index(settings):
    c, _ = make_client(settings, [])
    assert "ChipLlama" in c.get("/").text
    s = c.get("/api/status").json()
    assert s["device"] == "PIC24FJ256GB110" and s["flash_enabled"] is False and s["flash_tool"] == "PK5"


def test_api_generate(settings):
    c, m = make_client(settings, [fence(GOOD_T1)])
    r = c.post("/api/generate", json={"request": "Timer1 interrupt every 10 ms at 32 MHz priority 5", "use_images": False})
    j = r.json()
    assert r.status_code == 200 and j["status"] == "verified" and j["attempts"][0]["check"]["registers"]["PR1"] == "0x4E1F"


def test_api_calc_debug_export_flash(settings):
    c, _ = make_client(settings, [])
    assert c.post("/api/calc", json={"request": "Timer1 every 10 ms"}).json()["solution"]["period_reg"] == 19999
    assert c.post("/api/calc", json={"request": "do something"}).status_code == 422
    d = c.post("/api/debug", data={"dump": DUMP, "use_model": "false", "expected_period_ms": "10"}).json()
    assert any(f["code"] == "Timer1_OFF" for f in d["findings"])
    z = c.post("/api/export", json={"code": GOOD_T1, "request": "Timer1 every 10 ms"})
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "ChipLlamaProject/main.c" in names and "ChipLlamaProject/build.bat" in names
    f = c.post("/api/flash", json={"code": GOOD_T1, "confirm": True}).json()
    assert f["status"] == "disabled"


def test_api_debug_with_image_upload(settings):
    png = b"\x89PNG\r\n\x1a\n" + b"3" * 16
    c, m = make_client(settings, ['{"T1CON":"0x8010"}', "ok"])
    r = c.post("/api/debug", data={"dump": ""}, files=[("images", ("s.png", png, "image/png"))]).json()
    assert r["registers"] == {"T1CON": "0x8010"} and r["explanation"] == "ok"
