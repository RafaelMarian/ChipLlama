import pytest

from chipllama import board, prompts
from chipllama.checker import check_timer
from chipllama.timers import TimerSpec
from conftest import GOOD_T1, CFG

SPEC = TimerSpec(timer=1, period_s=0.010, fosc_hz=32e6, interrupt=True, priority=5)


def codes(rep, sev="error"):
    return {f.code for f in rep.findings if f.severity == sev}


def test_good_code_with_pragmas_passes_and_reports_clock_path():
    rep = check_timer(GOOD_T1, SPEC)
    assert rep.ok, rep.feedback_text()
    assert "CLOCK_PATH" in codes(rep, "info")


def test_missing_pragmas_is_an_error():
    code = "\n".join(l for l in GOOD_T1.splitlines() if not l.startswith("#pragma"))
    rep = check_timer(code, SPEC)
    assert "CONFIG_MISSING" in codes(rep)
    assert "CONFIG_MISSING" in rep.feedback_text()


def test_single_missing_setting():
    rep = check_timer(GOOD_T1.replace("#pragma config JTAGEN = OFF", ""), SPEC)
    f = next(f for f in rep.errors if f.code == "CONFIG_MISSING")
    assert f.expected == "JTAGEN"


def test_plldiv_div2_without_rcdiv0_is_wrong_pll_input():
    rep = check_timer(GOOD_T1.replace("PLLDIV = NODIV", "PLLDIV = DIV2"), SPEC)
    assert "PLL_INPUT" in codes(rep)


def test_plldiv_div2_with_rcdiv0_is_equivalent():
    code = GOOD_T1.replace("PLLDIV = NODIV", "PLLDIV = DIV2").replace("CLKDIVbits.RCDIV = 1;", "CLKDIVbits.RCDIV = 0;")
    assert check_timer(code, SPEC).ok


def test_nodiv_with_rcdiv0_gives_8mhz_pll_input():
    rep = check_timer(GOOD_T1.replace("CLKDIVbits.RCDIV = 1;", "CLKDIVbits.RCDIV = 0;"), SPEC)
    assert "PLL_INPUT" in codes(rep)


def test_reset_clkdiv_is_fine_for_nodiv():
    code = GOOD_T1.replace("CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; ", "")
    assert check_timer(code, SPEC).ok


def test_cpdiv_changes_fosc_and_mismatches_timing_math():
    rep = check_timer(GOOD_T1.replace("CLKDIVbits.CPDIV = 0;", "CLKDIVbits.CPDIV = 1;"), SPEC)
    f = next(f for f in rep.errors if f.code == "CLOCK_CONFIG")
    assert f.actual == "16 MHz" and f.expected == "32 MHz"


def test_frc_no_pll_mismatches_32mhz_but_matches_8mhz():
    code = GOOD_T1.replace("FNOSC = FRCPLL", "FNOSC = FRC")
    assert "CLOCK_CONFIG" in codes(check_timer(code, SPEC))
    code8 = code.replace("32000000UL", "8000000UL")
    spec8 = TimerSpec(timer=1, period_s=0.010, fosc_hz=8e6, interrupt=True, priority=5)
    assert check_timer(code8, spec8).ok


def test_posc_on_bare_board_rejected():
    rep = check_timer(GOOD_T1.replace("POSCMOD = NONE", "POSCMOD = HS").replace("FNOSC = FRCPLL", "FNOSC = PRIPLL"), SPEC)
    assert {"POSCMOD", "NO_CRYSTAL"} <= codes(rep)


def test_pll_off_rejected():
    assert "PLL_OFF" in codes(check_timer(GOOD_T1.replace("PLL_96MHZ = ON", "PLL_96MHZ = OFF"), SPEC))


def test_ics_mismatch_and_configurable_ics():
    assert "ICS" in codes(check_timer(GOOD_T1, SPEC, ics="PGx1"))
    assert check_timer(GOOD_T1.replace("ICS = PGx2", "ICS = PGx1"), SPEC, ics="PGx1").ok


def test_watchdog_and_jtag_must_be_off():
    assert "FWDTEN" in codes(check_timer(GOOD_T1.replace("FWDTEN = OFF", "FWDTEN = ON"), SPEC))
    assert "JTAGEN" in codes(check_timer(GOOD_T1.replace("JTAGEN = OFF", "JTAGEN = ON"), SPEC))


def test_invalid_names_and_values():
    rep = check_timer(GOOD_T1.replace("PLLDIV = NODIV", "PLLDIV = DIV7").replace("IESO = OFF", "IESO = OFF\n#pragma config FOO = BAR"), SPEC)
    assert {"CONFIG_VALUE", "CONFIG_UNKNOWN"} <= codes(rep)


def test_conflicting_duplicate():
    assert "CONFIG_SYNTAX" in codes(check_timer(GOOD_T1 + "\n#pragma config FNOSC = FRC\n", SPEC))


def test_comma_form_parsed():
    vals, probs = board.parse_pragmas("#pragma config FNOSC = FRCPLL, POSCMOD = NONE // c\n")
    assert vals == {"FNOSC": "FRCPLL", "POSCMOD": "NONE"} and not probs


def test_render_contains_all_required_and_ics():
    blk = board.render_pragmas(board.get_profile("frcpll"), "PGx3")
    vals, _ = board.parse_pragmas(blk)
    assert set(vals) == set(board.REQUIRED)
    assert vals["ICS"] == "PGx3" and vals["FNOSC"] == "FRCPLL" and vals["POSCMOD"] == "NONE"
    with pytest.raises(ValueError):
        board.render_pragmas(board.get_profile("frcpll"), "PGx4")


@pytest.mark.parametrize("key,fosc", [("frcpll", 32e6), ("frcpll_div2", 32e6), ("frc", 8e6), ("xtal", 32e6)])
def test_every_profile_renders_a_self_consistent_block(key, fosc):
    p = board.get_profile(key, 8e6)
    rcdiv = 0 if key == "frcpll_div2" else 1
    vals = board.pragma_values(p)
    assert board.derive_fosc(vals, rcdiv, 0, p.crystal_hz).fosc_hz == fosc == p.fosc_hz


def test_xtal_profile_plldiv():
    assert board.get_profile("xtal", 12e6).clock["PLLDIV"] == "DIV3"
    assert board.get_profile("xtal", 8e6).clock["POSCMOD"] == "XT"


def test_prompt_demands_pragmas():
    assert "ALWAYS start the file" in prompts.SYSTEM and "#pragma config" in prompts.SYSTEM
    up = prompts.first_user_prompt("blink", SPEC, None, "", board.get_profile("frcpll"), "PGx1")
    assert "#pragma config FNOSC = FRCPLL" in up and "#pragma config ICS = PGx1" in up
    assert "PGEC1/PGED1" in up and "CLKDIVbits.RCDIV" in up


def test_agent_sends_profile_and_rejects_missing_pragmas(tmp_path, monkeypatch):
    from chipllama.agent import run_agent
    from chipllama.config import Settings
    from conftest import ScriptedModel, fence, compile_ok
    bare = "\n".join(l for l in GOOD_T1.splitlines() if not l.startswith("#pragma"))
    m = ScriptedModel([fence(bare), fence(GOOD_T1)])
    r = run_agent("Timer1 interrupt every 10 ms priority 5", m, Settings(), use_images=False, compile_fn=compile_ok)
    assert r.status == "verified" and len(r.attempts) == 2
    assert "CONFIG_MISSING" in m.calls[1][-1].text
    assert "Fosc = 32 MHz" in " ".join(r.notes + r.spec.notes)
