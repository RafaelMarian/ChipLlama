from chipllama.agent import extract_code, run_agent
from chipllama.compiler import CompileResult, Diagnostic
from chipllama.models.base import ModelError
from conftest import GOOD_T1, ScriptedModel, compile_ok, fence

REQ = "Configure Timer1 to interrupt every 10 ms at 32 MHz with priority 5"
BAD_PERIOD = GOOD_T1.replace("TCKPS = 1", "TCKPS = 2")


def test_extract_code():
    assert extract_code("x\n```c\nint a;\n```\ny") == "int a;\n"
    assert extract_code("no code here") is None
    assert "#include" in extract_code("#include <xc.h>\nint main(void){}")


def test_verified_first_try(settings):
    m = ScriptedModel([fence(GOOD_T1)])
    r = run_agent(REQ, m, settings, compile_fn=compile_ok)
    assert r.status == "verified" and len(r.attempts) == 1 and r.compiled
    assert r.to_dict()["verification_level"] == "registers+compile"
    # the prompt carries the datasheet register facts
    user = m.calls[0][1].text
    assert "T1CON @0x0104 (datasheet p.162)" in user and "TCKPS" in user and "Fcy = 16 MHz" in user


def test_self_correction_after_checker_mismatch(settings):
    m = ScriptedModel([fence(BAD_PERIOD), fence(GOOD_T1)])
    r = run_agent(REQ, m, settings, compile_fn=compile_ok)
    assert r.status == "verified" and len(r.attempts) == 2
    assert not r.attempts[0].check.ok and r.attempts[1].check.ok
    feedback = m.calls[1][-1].text
    assert "PERIOD" in feedback and "PR=19999" in feedback          # checker findings were fed back
    assert m.calls[1][-2].role == "assistant"


def test_self_correction_after_compile_error(settings):
    broken = GOOD_T1.replace("timer1_init(void)", "timer1_init(void")  # syntax error
    errs = CompileResult("error", [Diagnostic("error", 9, 1, "expected ')' before '{' token")], "main.c:9:1: error: expected ')'")

    def compile_fn(code, s, **kw):
        return errs if code == broken else compile_ok(code, s)

    m = ScriptedModel([fence(broken), fence(GOOD_T1)])
    r = run_agent(REQ, m, settings, compile_fn=compile_fn)
    assert r.status == "verified" and len(r.attempts) == 2
    assert r.attempts[0].compile.status == "error"
    assert "expected ')'" in m.calls[1][-1].text and "xc16-gcc errors" in m.calls[1][-1].text


def test_gives_up_after_max_iters(settings):
    m = ScriptedModel([fence(BAD_PERIOD)] * 3)
    r = run_agent(REQ, m, settings, max_iters=3, compile_fn=compile_ok)
    assert r.status == "failed" and len(r.attempts) == 3
    assert r.to_dict()["verification_level"] == "none"


def test_compiler_missing_is_graceful(settings, monkeypatch):
    monkeypatch.setattr("chipllama.compiler.find_tool", lambda *a, **k: None)
    m = ScriptedModel([fence(GOOD_T1)])
    r = run_agent(REQ, m, settings)       # real compile_c -> skipped
    assert r.status == "verified" and r.attempts[0].compile.status == "skipped"
    assert r.to_dict()["verification_level"] == "registers only (compiler unavailable)"


def test_reply_without_code_is_retried(settings):
    m = ScriptedModel(["I think you should use a timer.", fence(GOOD_T1)])
    r = run_agent(REQ, m, settings, compile_fn=compile_ok)
    assert r.status == "verified" and any("no code block" in n for n in r.notes)


def test_model_error_reported(settings):
    r = run_agent(REQ, ScriptedModel([ModelError("HTTP 401 bad key", 401)]), settings, compile_fn=compile_ok)
    assert r.status == "error" and "401" in r.error


def test_unparseable_request_asks_model_to_extract(settings):
    m = ScriptedModel(['{"timer":1,"bits32":false,"period_ms":10,"fosc_mhz":32,"interrupt":true,"priority":5}', fence(GOOD_T1)])
    r = run_agent("make the timer go off at a hundred hertz-ish", m, settings, compile_fn=compile_ok)
    assert r.spec.period_s == 0.01 and r.status == "verified"
    assert any("extracted by the model" in n for n in r.notes)


def test_impossible_request_fails_without_calling_model(settings):
    m = ScriptedModel([])
    r = run_agent("Timer1 interrupt every 30 s", m, settings, compile_fn=compile_ok)
    assert r.status == "error" and "does not fit" in r.error and not m.calls


def test_images_attached_when_datasheet_available(settings, tmp_path, monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"1" * 16
    monkeypatch.setattr("chipllama.agent.load_page_images", lambda pages, s: ([(p, png) for p in pages], "attached"))
    m = ScriptedModel([fence(GOOD_T1)])
    run_agent(REQ, m, settings, compile_fn=compile_ok)
    msg = m.calls[0][1]
    assert len(msg.images) == 5 and "p162" in msg.text      # T1 block diagram+T1CON + IFS0/IEC0/IPC0 pages


def test_hint_flag_includes_calculator_result(settings):
    m = ScriptedModel([fence(GOOD_T1)])
    run_agent(REQ, m, settings, compile_fn=compile_ok, hint=True)
    assert "PR=19999" in m.calls[0][1].text
    m2 = ScriptedModel([fence(GOOD_T1)])
    run_agent(REQ, m2, settings, compile_fn=compile_ok)
    assert "PR=19999" not in m2.calls[0][1].text
