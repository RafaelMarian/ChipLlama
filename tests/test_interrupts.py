from conftest import CFG, GOOD_T1, ScriptedModel, compile_ok, fence

from chipllama import cli
from chipllama.agent import run_agent
from chipllama.cemu import emulate
from chipllama.checker import CheckReport
from chipllama.interrupts import parse_request, audit
from chipllama.irq import vector
from chipllama.peripherals import check_full

REQ = "External interrupt INT1 on RP10, falling edge, priority 6"

GOOD = CFG + """
volatile unsigned int hits;
static void int1_init(void)
{
    TRISFbits.TRISF4 = 1;                    /* RP10 = RF4 */
    __builtin_write_OSCCONL(OSCCON & 0xBF);
    RPINR0bits.INT1R = 10;
    __builtin_write_OSCCONL(OSCCON | 0x40);
    INTCON2bits.INT1EP = 1;                  /* falling edge */
    IPC5bits.INT1IP = 6;
    IFS1bits.INT1IF = 0;
    IEC1bits.INT1IE = 1;
}
void __attribute__((interrupt, auto_psv)) _INT1Interrupt(void) { hits++; IFS1bits.INT1IF = 0; }
int main(void) { CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; int1_init(); while (1) { } }
"""


def errs(code, spec=None):
    return {f.code for f in check_full(code, {"irq": spec or parse_request(REQ)}, fcy=16e6).errors}


def test_vector_math_matches_datasheet():
    v = vector("_U1RXInterrupt")
    assert (v.irq, v.ifs, v.iec, v.ipc) == (11, ("IFS0", "U1RXIF"), ("IEC0", "U1RXIE"), ("IPC2", "U1RXIP"))
    v = vector("IC9Interrupt")
    assert (v.irq, v.ipc) == (93, ("IPC23", "IC9IP"))
    assert vector("ADC1").ipc == ("IPC3", "AD1IP")


def test_parse():
    s = parse_request(REQ)
    assert [r.vec.name for r in s.reqs] == ["INT1Interrupt"]
    r = s.reqs[0]
    assert (r.priority, r.edge, r.pin_rp) == (6, "falling", 10)
    assert parse_request("Timer1 interrupt every 10 ms") is None           # owned by the timer checker
    assert parse_request("U2RX interrupt").reqs[0].vec.isr == "_U2RXInterrupt"


def test_good_sample():
    rep = check_full(GOOD, {"irq": parse_request(REQ)}, fcy=16e6)
    assert rep.ok, rep.errors


def test_rules():
    assert "INT_EDGE" in errs(GOOD.replace("INTCON2bits.INT1EP = 1;", ""))
    assert "INT_PPS" in errs(GOOD.replace("RPINR0bits.INT1R = 10;", "RPINR0bits.INT1R = 11;"))
    assert "INT_TRIS" in errs(GOOD.replace("TRISFbits.TRISF4 = 1;", "TRISFbits.TRISF4 = 0;"))
    assert "IP" in errs(GOOD.replace("IPC5bits.INT1IP = 6;", "IPC5bits.INT1IP = 0;"))
    assert "ISR_FLAG" in errs(GOOD.replace(" IFS1bits.INT1IF = 0; }", " }"))


def test_audit_bad_isr_name_with_suggestion():
    bad = GOOD.replace("_INT1Interrupt", "_Int1Interrupt")
    rep = check_full(bad, {"irq": parse_request(REQ)}, fcy=16e6)
    f = next(f for f in rep.errors if f.code == "ISR_NAME")
    assert "_INT1Interrupt" in f.message
    bad = GOOD_T1.replace("_T1Interrupt", "_Timer1Interrupt")
    rep = CheckReport(); audit(bad, rep, emulate(bad))
    assert {"ISR_NAME", "IE_NO_ISR"} <= {f.code for f in rep.errors}


def test_audit_runs_for_timer_requests(settings):
    bad = GOOD_T1.replace("void __attribute__((interrupt", "void __attribute__((interrupt, auto_psv)) _U1RXInterrupt(void){}\nvoid __attribute__((interrupt", 1) \
        .replace("IEC0bits.T1IE = 1;", "IEC0bits.T1IE = 1; IEC0bits.SPI1IE = 1;")
    m = ScriptedModel([fence(bad), fence(GOOD_T1)])
    r = run_agent("Configure Timer1 to interrupt every 10 ms at 32 MHz with priority 5", m, settings, compile_fn=compile_ok)
    assert r.status == "verified" and len(r.attempts) == 2
    assert "IE_NO_ISR" in {f.code for f in r.attempts[0].check.errors}


def test_agent_int1(settings):
    m = ScriptedModel([fence(GOOD)])
    r = run_agent(REQ, m, settings, compile_fn=compile_ok)
    assert r.status == "verified", r.attempts and r.attempts[-1].check.errors
    assert r.spec is None and "INT1R" in m.calls[0][1].text


def test_cli(capsys):
    assert cli.main(["calc", "irq", "U3TX"]) == 0
    out = capsys.readouterr().out
    assert "IRQ 83" in out and "IPC20" in out
