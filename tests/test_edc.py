"""EDC (Microchip .PIC) driven device support: parser, pragma generation, checker, compiler selection, CLI, API.
Uses trimmed fixtures in tests/fixtures; full pack files (if present in /workspace/edc or $CHIPLLAMA_EDC_DIR)
get extra checks that are skipped otherwise."""
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chipllama import chipdb, cli, edc, pragmas, prompts
from chipllama.checker import CheckReport, check_device
from chipllama.compiler import COMPILERS, compile_c, find_compiler
from chipllama.config import Settings
from chipllama.flash import part_name
from chipllama.server import create_app
from conftest import GOOD_T1, ScriptedModel, compile_ok, fence

FIX = Path(__file__).parent / "fixtures"
DEVS = ["PIC18F45K22", "DSPIC33CK256MP506", "PIC24FJ256GB110"]
FULL = Path(os.environ.get("CHIPLLAMA_EDC_DIR", "/workspace/edc"))


def dev(name):
    return edc.parse_edc(FIX / f"{name}.PIC")


@pytest.fixture
def packs(tmp_path):
    """Fake MPLAB X packs tree with two pack versions (newest must win)."""
    for fam, name in (("PIC18F-K_DFP", "PIC18F45K22"), ("dsPIC33CK-MP_DFP", "DSPIC33CK256MP506"),
                      ("PIC24F-GA-GB_DFP", "PIC24FJ256GB110")):
        for ver in ("1.2.0", "1.10.3"):
            d = tmp_path / "Microchip" / fam / ver / "edc"
            d.mkdir(parents=True)
            shutil.copy(FIX / f"{name}.PIC", d / f"{name}.PIC")
    edc.index_devices(str(tmp_path), refresh=True)
    chipdb._DEV_CACHE.clear()
    yield tmp_path
    chipdb._DEV_CACHE.clear()


# ------------------------------------------------------------------ parser
def test_families_and_compilers():
    assert [(edc.family_of(n), edc.compiler_for(n)) for n in DEVS] == [
        ("PIC18", "xc8"), ("dsPIC33", "xc16"), ("PIC24", "xc16")]
    assert edc.compiler_for("PIC32MX795F512L") == "xc32" and edc.compiler_for("PIC16F18877") == "xc8"
    assert edc.compiler_cpu("DSPIC33CK256MP506") == "33CK256MP506" and edc.compiler_cpu("PIC18F45K22") == "18F45K22"
    assert part_name("DSPIC33CK256MP506") == "33CK256MP506" and part_name("PIC24FJ256GB110") == "24FJ256GB110"


@pytest.mark.parametrize("name", DEVS)
def test_parse_all_three(name):
    d = dev(name)
    assert d.name == name and d.settings and d.sfrs and d.program_bytes > 0
    assert d.icsp_pairs and 1 in d.icsp_pairs
    t1 = d.sfr("T1CON")
    assert t1 is not None and t1.fields
    r = edc.Device.from_json(d.to_json())
    assert r.name == d.name and len(r.settings) == len(d.settings) and set(r.sfrs) == set(d.sfrs)


def test_bit_positions():
    p18 = dev("PIC18F45K22").sfr("T1CON")
    f = {x.name: x for x in p18.fields}
    assert f["TMR1ON"].lsb == 0 and f["T1CKPS"].lsb == 4 and f["T1CKPS"].width == 2
    p24 = {x.name: x for x in dev("PIC24FJ256GB110").sfr("T1CON").fields}
    assert p24["TON"].lsb == 15 and p24["TCKPS"].width == 2
    assert "TCKPS0" in dev("PIC24FJ256GB110").sfr("T1CON").field_names()


def test_config_settings_values():
    d18 = dev("PIC18F45K22")
    fosc = d18.setting("FOSC")
    assert fosc and "INTIO67" in fosc.allowed() and fosc.accepts("INTIO67") and not fosc.accepts("FRCPLL")
    assert dev("DSPIC33CK256MP506").setting("FNOSC").accepts("FRC")
    assert dev("DSPIC33CK256MP506").icsp_pairs == [1, 2, 3]
    bslim = dev("DSPIC33CK256MP506").setting("BSLIM")
    assert bslim.range and bslim.accepts("0x1FFF")


def test_json_cache(tmp_path):
    p = tmp_path / "PIC18F45K22.PIC"
    shutil.copy(FIX / "PIC18F45K22.PIC", p)
    a = edc.load_device("PIC18F45K22", path=str(p), cache_dir=str(tmp_path / "c"))
    files = list((tmp_path / "c" / "edc").glob("PIC18F45K22-*.json"))
    assert len(files) == 1
    b = edc.load_device("PIC18F45K22", path=str(p), cache_dir=str(tmp_path / "c"))
    assert a.to_json() == b.to_json()


# ------------------------------------------------------------------ discovery
def test_index_newest_version_and_search(packs):
    hits = edc.search_devices("45k", str(packs))
    assert [h["name"] for h in hits] == ["PIC18F45K22"]
    assert "1.10.3" in hits[0]["path"] and hits[0]["compiler"] == "xc8"
    assert len(edc.search_devices("", str(packs))) == 3


def test_get_device_and_builtin_fallback(packs, tmp_path):
    d = chipdb.get_device("PIC18F45K22", str(packs), str(tmp_path / "c"))
    assert d.family == "PIC18" and d.source.endswith("PIC18F45K22.PIC")
    empty = tmp_path / "nopacks"; empty.mkdir()
    edc.index_devices(str(empty), refresh=True)
    b = chipdb.get_device("PIC24FJ256GB110", str(empty), str(tmp_path / "c"))
    assert b.source == "built-in" and b.setting("FNOSC")
    assert chipdb.get_device("PIC18F45K22", str(empty), str(tmp_path / "c")) is None


# ------------------------------------------------------------------ pragmas
@pytest.mark.parametrize("name", DEVS)
def test_render_is_complete_and_valid(name):
    d = dev(name)
    text = pragmas.render(d, "PGx2")
    rep = CheckReport()
    check_device("#include <xc.h>\n" + text + "\nint main(void){while(1);}\n", d, ics="PGx2", rep=rep)
    assert rep.ok, rep.feedback_text()
    vals = pragmas.default_values(d, "PGx2")
    for s in d.visible_settings():
        assert s.name in vals
    low = {k: v for k, v in vals.items() if k.startswith(("CP", "WRT", "EBTR"))}
    assert all(v == "OFF" for v in low.values())


def test_safe_defaults_per_family():
    v18 = pragmas.default_values(dev("PIC18F45K22"))
    assert v18["FOSC"] == "INTIO67" and v18["WDTEN"] == "OFF" and v18["DEBUG"] == "OFF" and v18["XINST"] == "OFF"
    v33 = pragmas.default_values(dev("DSPIC33CK256MP506"), "PGx2")
    assert v33["FNOSC"] == "FRC" and v33["ICS"] == "PGD2" and v33["JTAGEN"] == "OFF" and v33["DMTDIS"] == "OFF"
    assert pragmas.default_values(dev("DSPIC33CK256MP506"), "PGx3")["ICS"] == "PGD3"
    v24 = pragmas.default_values(dev("PIC24FJ256GB110"), "PGx1")
    assert v24["FWDTEN"] == "OFF" and v24["ICS"] == "PGx1" and v24["JTAGEN"] == "OFF"


def _codes(code, d, **kw):
    rep = CheckReport()
    check_device(code, d, rep=rep, **kw)
    return {f.code for f in rep.findings if f.severity == "error"}, rep


def test_checker_flags_bad_pragmas():
    d = dev("PIC18F45K22")
    base = pragmas.render(d)
    assert _codes(base, d)[0] == set()
    assert "CONFIG_VALUE" in _codes(base.replace("FOSC = INTIO67", "FOSC = FRCPLL"), d)[0]
    assert "CONFIG_UNKNOWN" in _codes(base + "\n#pragma config FNOSC = FRC\n", d)[0]
    missing = "\n".join(l for l in base.splitlines() if "FOSC =" not in l)
    assert "CONFIG_MISSING" in _codes(missing, d)[0]
    nowdt = "\n".join(l for l in base.splitlines() if "WDTPS" not in l)
    errs, rep = _codes(nowdt, d)
    assert not errs and any(f.code == "CONFIG_INCOMPLETE" for f in rep.findings)


def test_checker_ics_mismatch_dspic():
    d = dev("DSPIC33CK256MP506")
    code = pragmas.render(d, "PGx2")
    assert _codes(code, d, ics="PGx3")[0] == {"ICS"}
    assert _codes(code, d, ics="PGx2")[0] == set()


def test_checker_sfr_fields():
    d = dev("PIC18F45K22")
    good = pragmas.render(d) + "\nvoid f(void){T1CONbits.TMR1ON=1;T1CONbits.T1CKPS=3;T1CONbits.T1CKPS1=1;PIR1bits.TMR1IF=0;}\n"
    assert _codes(good, d)[0] == set()
    bad = pragmas.render(d) + "\nvoid f(void){T1CONbits.TON=1;FOOREGbits.X=1;}\n"
    errs = _codes(bad, d)[0]
    assert "SFR_FIELD" in errs and "SFR_UNKNOWN" in errs
    d33 = dev("DSPIC33CK256MP506")
    ok33 = pragmas.render(d33) + "\nvoid f(void){T1CONbits.TON=1;T1CONbits.TCKPS=2;IFS0bits.T1IF=0;IEC0bits.T1IE=1;}\n"
    assert _codes(ok33, d33)[0] == set()


def test_builtin_good_code_passes_edc_check():
    d = dev("PIC24FJ256GB110")
    errs, rep = _codes(GOOD_T1, d, require_config=False)
    assert errs == set(), rep.feedback_text()


# ------------------------------------------------------------------ prompts
def test_generic_prompt_injects_config_and_sfrs():
    from chipllama.timers import parse_request
    d = dev("PIC18F45K22")
    spec = parse_request("Timer1 interrupt every 10 ms with 16 MHz clock")
    sysm, user = prompts.generic_system(d), prompts.generic_user_prompt("Timer1 every 10 ms", spec, d)
    assert "XC8" in sysm and "Fosc/4" in sysm
    assert "#pragma config FOSC = INTIO67" in user and "T1CON" in user and "PIR1" in user and "CONFIG1H.FOSC" in user
    d33 = dev("DSPIC33CK256MP506")
    u33 = prompts.generic_user_prompt("Timer1 every 10 ms", spec, d33)
    assert "IFS0" in u33 and "FNOSC" in u33 and "XC16" in prompts.generic_system(d33)


# ------------------------------------------------------------------ compiler selection
def test_compiler_selection(monkeypatch, tmp_path):
    seen = {}

    def fake_find(name, configured="", compiler=None):
        seen[name] = compiler
        return f"/x/{name}"
    monkeypatch.setattr("chipllama.compiler.find_tool", fake_find)
    calls = []

    class P:
        returncode, stdout, stderr = 0, "", ""

    def runner(cmd, **kw):
        calls.append(cmd); return P()
    for devn, drv, flag in (("PIC18F45K22", "xc8-cc", "-mcpu=18F45K22"),
                            ("PIC32MX795F512L", "xc32-gcc", "-mprocessor=32MX795F512L"),
                            ("DSPIC33CK256MP506", "xc16-gcc", "-mcpu=33CK256MP506")):
        s = Settings(device=devn, mplab_packs_dir=str(tmp_path), dspic_compiler="xc16")
        assert find_compiler(s)[0] == {"xc8-cc": "xc8", "xc32-gcc": "xc32", "xc16-gcc": "xc16"}[drv]
        r = compile_c("int main(void){return 0;}", s, runner=runner, workdir=str(tmp_path))
        assert r.status == "ok" and calls[-1][0] == f"/x/{drv}" and flag in calls[-1]


def test_compiler_missing_message(monkeypatch, tmp_path):
    monkeypatch.setattr("chipllama.compiler.find_tool", lambda *a, **k: None)
    r = compile_c("x", Settings(device="PIC18F45K22"), workdir=str(tmp_path))
    assert r.status == "skipped" and "xc8-cc" in r.reason and "XC8_CC" in r.reason


# ------------------------------------------------------------------ CLI / API / agent
def test_cli_devices_and_pragmas(packs, capsys, monkeypatch):
    monkeypatch.setenv("MPLAB_PACKS_DIR", str(packs))
    assert cli.main(["devices", "dspic"]) == 0
    out = capsys.readouterr().out
    assert "DSPIC33CK256MP506" in out and "xc16" in out
    assert cli.main(["pragmas", "PIC18F45K22"]) == 0
    assert "#pragma config FOSC = INTIO67" in capsys.readouterr().out
    assert cli.main(["pragmas", "PIC18F45K22", "--edc", str(FIX / "PIC18F45K22.PIC")]) == 0
    assert cli.main(["pragmas", "PIC18F99Q99"]) == 2


def test_api_devices_device_pragmas(packs):
    s = Settings(device="PIC18F45K22", mplab_packs_dir=str(packs))
    c = TestClient(create_app(s, model_factory=lambda: None, compile_fn=compile_ok))
    j = c.get("/api/devices?q=K22").json()
    assert j["devices"][0]["name"] == "PIC18F45K22" and "path" not in j["devices"][0]
    d = c.get("/api/device?name=DSPIC33CK256MP506").json()
    assert d["family"] == "dsPIC33" and d["compiler"] == "xc16" and "#pragma config FNOSC = FRC" in d["pragmas"]
    p = c.get("/api/pragmas").json()
    assert "#pragma config FOSC = INTIO67" in p["text"]
    assert c.get("/api/device?name=PIC18F99Q99").status_code == 404
    rows = c.get("/api/doctor").json()["rows"]
    assert any("PIC18F45K22.PIC" in str(r) and "XC8" in str(r) for r in rows)


def test_agent_generic_device(packs):
    d = dev("PIC18F45K22")
    good = ("#include <xc.h>\n" + pragmas.render(d) + "\n#define _XTAL_FREQ 16000000\n"
            "void __interrupt() isr(void){ if(PIR1bits.TMR1IF){ PIR1bits.TMR1IF=0; } }\n"
            "int main(void){ T1CONbits.T1CKPS=3; PIE1bits.TMR1IE=1; T1CONbits.TMR1ON=1; while(1); }\n")
    bad = good.replace("TMR1ON", "TON")
    from chipllama.agent import run_agent
    m = ScriptedModel([fence(bad), fence(good)])
    s = Settings(device="PIC18F45K22", mplab_packs_dir=str(packs))
    r = run_agent("Timer1 interrupt every 10 ms at 16 MHz", m, s, compile_fn=compile_ok, max_iters=3)
    assert r.status == "verified", r.to_dict()
    assert len(r.attempts) == 2 and "SFR_FIELD" in r.attempts[0].check.feedback_text()


# ------------------------------------------------------------------ full pack files (optional)
@pytest.mark.parametrize("name", DEVS)
def test_full_samples(name):
    p = FULL / f"{name}.PIC"
    if not p.is_file():
        pytest.skip("full EDC sample not present")
    d = edc.parse_edc(p)
    assert len(d.sfrs) > 150 and len(d.visible_settings()) >= len(dev(name).visible_settings())
    rep = CheckReport()
    check_device(pragmas.render(d), d, rep=rep)
    assert rep.ok, rep.feedback_text()
