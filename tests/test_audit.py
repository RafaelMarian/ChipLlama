import io
import json
import zipfile
from pathlib import Path

import pytest

from chipllama import audit as au
from chipllama.cli import main as cli_main

EX = Path(__file__).resolve().parent.parent / "examples" / "audit"
GOOD = """#include <xc.h>
#pragma config FWDTEN = OFF
#pragma config JTAGEN = OFF
#pragma config ICS = PGx2
#pragma config FNOSC = FRCPLL
#pragma config PLL_96MHZ = ON
#pragma config PLLDIV = NODIV
#pragma config POSCMOD = NONE
#define FCY 16000000UL
#include <libpic30.h>
volatile unsigned int ticks;
void __attribute__((interrupt, no_auto_psv)) _T1Interrupt(void)
{
    ticks++;
    IFS0bits.T1IF = 0;
}
int main(void)
{
    CLKDIVbits.RCDIV = 1;
    CLKDIVbits.CPDIV = 0;
    TRISAbits.TRISA0 = 0;
    PR1 = 31249;
    T1CONbits.TCKPS = 3;
    IFS0bits.T1IF = 0;
    IEC0bits.T1IE = 1;
    T1CONbits.TON = 1;
    while (1) { if (ticks > 10) { ticks = 0; LATAbits.LATA0 ^= 1; } __delay_ms(1); }
}
"""


@pytest.mark.parametrize("name", ["blinky_buggy", "uart_sensor.X"])
def test_examples_match_expected(name):
    exp = json.loads((EX / name / "expected.json").read_text())
    rep = au.audit_path(EX / name)
    got = [{"rule": f.rule, "severity": f.severity, "file": f.file, "line": f.line} for f in rep.findings]
    assert got == exp["findings"]
    assert rep.score == exp["score"]
    assert rep.project == exp["project"]
    assert au.audit_files(rep.patched()).score == exp["score_after_fixes"]


def test_mplabx_project_detected_and_files_loaded():
    files, kind, dev = au.load_path(EX / "uart_sensor.X")
    assert kind == "mplabx" and dev == "PIC24FJ256GB110"
    assert set(files) == {"main.c", "uart.c", "uart.h"}


def test_blinky_fixes_reach_100():
    rep = au.audit_path(EX / "blinky_buggy")
    fixed = au.audit_files(rep.patched())
    assert fixed.score == 100 and not fixed.findings
    assert "#define FCY 16000000UL" in rep.patched()["main.c"]
    assert "_T1Interrupt" in rep.patched()["main.c"]


def test_every_finding_cites_pages_and_location():
    for name in ("blinky_buggy", "uart_sensor.X"):
        for f in au.audit_path(EX / name).findings:
            assert f.pages or f.rule == "VOLATILE", f.rule  # volatile is C, not datasheet
            assert f.line >= 1 and f.file
            assert f.pages == sorted(set(f.pages))


def test_clean_code_scores_100():
    rep = au.audit_files({"main.c": GOOD})
    assert [f.rule for f in rep.findings] == []
    assert rep.score == 100 and rep.fcy == 16e6


def test_missing_config_flagged():
    rep = au.audit_files({"main.c": "#include <xc.h>\nint main(void){while(1);}\n"})
    assert "CFG_MISSING" in {f.rule for f in rep.findings}


def test_wdt_ok_when_clrwdt_called():
    src = GOOD.replace("FWDTEN = OFF", "FWDTEN = ON").replace("__delay_ms(1);", "__delay_ms(1); ClrWdt();")
    assert "WDT_RESET" not in {f.rule for f in au.audit_files({"main.c": src}).findings}


def test_wrong_fcy_and_jtag():
    src = GOOD.replace("FCY 16000000UL", "FCY 4000000UL").replace("JTAGEN = OFF", "JTAGEN = ON")
    rules = {f.rule for f in au.audit_files({"main.c": src}).findings}
    assert {"FCY_WRONG", "JTAG_PINS"} <= rules


def test_volatile_and_isr_flag():
    src = GOOD.replace("volatile unsigned int ticks", "unsigned int ticks").replace("    IFS0bits.T1IF = 0;\n}", "}")
    rules = {f.rule for f in au.audit_files({"main.c": src}).findings}
    assert {"VOLATILE", "ISR_FLAG"} <= rules


def test_ics_mismatch_uses_board_setting():
    assert "ICS_MISMATCH" in {f.rule for f in au.audit_files({"main.c": GOOD}, ics="PGx1").findings}


def test_fixes_zip_contents():
    rep = au.audit_path(EX / "blinky_buggy")
    z = zipfile.ZipFile(io.BytesIO(au.fixes_zip(rep)))
    assert {"main.c", "CHIPLLAMA_AUDIT.md", "chipllama_fixes.diff"} <= set(z.namelist())


def test_load_zip_reads_device():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("p.X/main.c", GOOD)
        z.writestr("p.X/nbproject/configurations.xml", "<targetDevice>PIC24FJ128GA010</targetDevice>")
        z.writestr("p.X/build/x.c", "junk")
    files = au.load_zip(buf.getvalue())
    assert files["__device__"] == "PIC24FJ128GA010" and "p.X/main.c" in files and "p.X/build/x.c" not in files


def test_enrich_explain_with_fake_model():
    class M:
        def complete(self, msgs):
            assert "ISR_NAME" in msgs[0].text
            return "review text"
    rep = au.enrich(au.audit_path(EX / "blinky_buggy"), M(), None, explain=True)
    assert rep.explanation == "review text"


def test_cli_audit(tmp_path, capsys):
    rc = cli_main(["audit", str(EX / "blinky_buggy"), "--json", "--fix", str(tmp_path / "out")])
    assert rc == 1
    d = json.loads(capsys.readouterr().out)
    assert d["score"] < 50 and (tmp_path / "out" / "main.c").exists()
    assert cli_main(["audit", str(tmp_path / "out")]) == 0
    assert cli_main(["audit", str(EX / "blinky_buggy"), "--fix", str(EX / "blinky_buggy")]) == 2


def test_server_audit_endpoint():
    from fastapi.testclient import TestClient
    from chipllama.config import Settings
    from chipllama.server import create_app
    c = TestClient(create_app(Settings()))
    ex = c.get("/api/audit/examples").json()
    assert {e["name"] for e in ex} >= {"blinky_buggy", "uart_sensor.X"}
    src = (EX / "blinky_buggy" / "main.c").read_text()
    r = c.post("/api/audit", data={"code": src}).json()
    assert r["counts"]["error"] >= 4 and r["diff"]
    z = c.get(f"/api/audit/{r['id']}/fixes.zip")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    assert c.post("/api/audit", data={"code": ""}).status_code == 422
