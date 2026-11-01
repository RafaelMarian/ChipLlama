import json
import pytest
from chipllama import uart as u, faults
from chipllama.agent import run_agent
from conftest import CFG, GOOD_T1, ScriptedModel, compile_ok, fence

UREQ = "UART1 115200 on RP10 TX / RP17 RX, print hello every 1 s"
BODY = """
#define FCY 16000000UL
#include <libpic30.h>
void uart1_init(void)
{
    __builtin_write_OSCCONL(OSCCON & 0xBF);   /* unlock PPS */
    RPINR18bits.U1RXR = 17;                   /* U1RX <- RP17 (RF5) */
    RPOR5bits.RP10R = 3;                      /* RP10 (RF4) -> U1TX */
    __builtin_write_OSCCONL(OSCCON | 0x40);   /* lock PPS */
    TRISFbits.TRISF4 = 0;
    TRISFbits.TRISF5 = 1;
    U1MODE = 0;
    U1MODEbits.BRGH = 1;
    U1BRG = 34;
    U1MODEbits.UARTEN = 1;
    U1STAbits.UTXEN = 1;
}
void putc1(char c) { while (U1STAbits.UTXBF); U1TXREG = c; }
int main(void)
{
    CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0;
    uart1_init();
    while (1) { const char *s = "hello\\r\\n"; while (*s) putc1(*s++); __delay_ms(1000); }
}
"""
GOOD_UART = "#include <xc.h>\n" + CFG + BODY


def spec():
    return u.parse_uart_request(UREQ, 16e6)


def test_baud_115200():
    s = u.solve_baud(16e6, 115200)
    assert (s.brgh, s.brg) == (1, 34) and abs(s.error_pct + 0.79) < 0.01
    o = {x.brgh: x for x in u.baud_options(16e6, 115200)}
    assert o[0].brg == 8 and abs(o[0].error_pct) > 3


def test_baud_9600_picks_lowest_error():
    s = u.solve_baud(16e6, 9600)
    assert (s.brgh, s.brg) == (1, 416) and abs(s.error_pct) < 0.1   # BRGH=0/103 is +0.16%


def test_parse_request():
    s = spec()
    assert s.baud == 115200 and s.tx_rp == 10 and s.rx_rp == 17 and s.uart == 1
    assert u.port_pin(10) == "RF4" and u.port_pin(17) == "RF5"
    assert u.parse_uart_request("Timer1 every 10 ms") is None
    assert u.rpor_field(10) == ("RPOR5", "RP10R") and u.rpor_field(11) == ("RPOR5", "RP11R")


def test_checker_good():
    rep = u.check_uart_full(GOOD_UART, spec())
    errs = [f for f in rep.findings if f.severity == "error"]
    assert rep.ok, errs


@pytest.mark.parametrize("mut,code", [
    (("U1BRG = 34", "U1BRG = 69"), "BRG"),
    (("RPOR5bits.RP10R = 3", "RPOR5bits.RP11R = 3"), "PPS_TX"),
    (("RPINR18bits.U1RXR = 17", "RPINR18bits.U1RXR = 16"), "PPS_RX"),
    (("U1MODEbits.UARTEN = 1;", ""), "UARTEN"),
    (("U1STAbits.UTXEN = 1;", ""), "UTXEN"),
    (("__builtin_write_OSCCONL(OSCCON & 0xBF);", "__builtin_write_OSCCONL(OSCCON | 0x40);"), "PPS_LOCKED"),
])
def test_checker_bad(mut, code):
    rep = u.check_uart_full(GOOD_UART.replace(*mut), spec())
    assert not rep.ok
    assert code in {f.code for f in rep.findings if f.severity == "error"}


def test_analog_rx_pin_needs_ad1pcfg():
    # RP0 = RB0 = AN0
    s = u.parse_uart_request("UART1 9600 TX RP10 RX RP0", 16e6)
    code = GOOD_UART.replace("U1RXR = 17", "U1RXR = 0").replace("TRISFbits.TRISF5 = 1", "TRISBbits.TRISB0 = 1").replace("U1BRG = 34", "U1BRG = 103").replace("U1MODEbits.BRGH = 1;", "")
    rep = u.check_uart_full(code, s)
    assert "ANALOG_RX" in {f.code for f in rep.findings if f.severity == "error"}
    ok = u.check_uart_full(code.replace("TRISBbits.TRISB0 = 1;", "TRISBbits.TRISB0 = 1; AD1PCFG = 0xFFFF;"), s)
    assert "ANALOG_RX" not in {f.code for f in ok.findings}


def test_faults_are_labelled_and_diffed():
    f = faults.inject_fault(GOOD_UART, uart=True)
    assert f and "U1BRG" in f.corrupted and f.corrupted != f.original and "-" in f.diff
    assert faults.DEMO_LABEL == "DEMO FAULT INJECTION"
    t = faults.inject_fault(GOOD_T1, timer=1)
    assert t and t.corrupted != GOOD_T1


def test_agent_uart_verified(settings):
    m = ScriptedModel([fence(GOOD_UART)])
    r = run_agent(UREQ, m, settings, compile_fn=compile_ok)
    assert r.status == "verified" and len(r.attempts) == 1
    d = r.to_dict()
    assert d["uart"]["baud"] == 115200 and not d["demo_fault_injection"]
    user = m.calls[0][1].text
    assert "U1BRG" in user and "RPINR18" in user


@pytest.mark.parametrize("req,good", [(UREQ, GOOD_UART),
                                      ("Configure Timer1 to interrupt every 10 ms at 32 MHz with priority 5", GOOD_T1)])
def test_agent_inject_fault_fails_then_fixes(settings, req, good):
    events = []
    m = ScriptedModel([fence(good), fence(good)])
    r = run_agent(req, m, settings, compile_fn=compile_ok, inject_fault=True,
                  on_event=lambda e, d: events.append((e, d)))
    assert r.status == "verified" and len(r.attempts) == 2
    assert not r.attempts[0].check.ok and r.attempts[0].injected_fault
    assert r.attempts[1].injected_fault is None
    names = [e for e, _ in events]
    assert names.index("fault") < names.index("reflect")
    fd = dict(events)["fault"]
    assert "DEMO" in fd["label"] and fd["diff"]
    assert r.to_dict()["demo_fault_injection"]


def test_cli_calc_uart(capsys):
    from chipllama.cli import main
    assert main(["calc", "uart", "--baud", "115200", "--tx", "RP10", "--rx", "RP17"]) == 0
    out = capsys.readouterr().out
    assert "U1BRG=34" in out and "<- best" in out and "RPOR5bits.RP10R = 3" in out and "U1RXR = 17" in out


def test_cli_generate_inject_fault(capsys, monkeypatch, tmp_path):
    from chipllama import cli
    m = ScriptedModel([fence(GOOD_UART), fence(GOOD_UART), "explanation", "explanation"])
    monkeypatch.setattr(cli, "make_model", lambda *a, **k: m)
    monkeypatch.setattr("chipllama.agent.compile_c", compile_ok)
    monkeypatch.setenv("DATASHEET_PDF", str(tmp_path / "none.pdf"))
    rc = cli.main(["generate", UREQ, "--inject-fault", "--no-images", "--out", str(tmp_path / "o.c")])
    out = capsys.readouterr().out
    assert rc == 0 and "[DEMO FAULT INJECTION]" in out and "[reflect]" in out
    assert out.strip().splitlines()[-1].startswith("VERIFIED")


def test_api_stream_inject_fault(settings):
    from fastapi.testclient import TestClient
    from chipllama.server import create_app
    m = ScriptedModel([fence(GOOD_UART), fence(GOOD_UART)])
    c = TestClient(create_app(settings, model_factory=lambda b: m, compile_fn=compile_ok))
    r = c.post("/api/generate/stream", json={"request": UREQ, "use_images": False, "inject_fault": True})
    evs = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    names = [e["event"] for e in evs]
    assert "fault" in names and "reflect" in names and names[-1] == "done"
    assert evs[-1]["data"]["status"] == "verified" and evs[-1]["data"]["demo_fault_injection"]
    att = [e["data"] for e in evs if e["event"] == "attempt"]
    assert att[1]["diff"]                       # the fix diff is shown
