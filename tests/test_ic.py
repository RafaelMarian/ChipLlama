import pytest
from conftest import CFG, ScriptedModel, compile_ok, fence

from chipllama import cli
from chipllama.agent import run_agent
from chipllama.ic import parse_request, calc_lines
from chipllama.peripherals import check_full

MAIN = "int main(void) { CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; ic_init(); while (1) { } }\n"
REQ = "Measure period on RP10 with IC1 rising edges using Timer2, up to 20 ms, priority 5"
GOOD = CFG + """
volatile unsigned int last, period;
static void ic_init(void)
{
    TRISFbits.TRISF4 = 1;                 /* RP10 = RF4 */
    __builtin_write_OSCCONL(OSCCON & 0xBF);
    RPINR7bits.IC1R = 10;
    __builtin_write_OSCCONL(OSCCON | 0x40);
    T2CON = 0; TMR2 = 0; PR2 = 0xFFFF;
    T2CONbits.TCKPS = 1;                  /* 1:8 -> 0.5 us tick, 32.8 ms range */
    IC1CON1 = 0; IC1CON2 = 0;             /* free-running IC1TMR */
    IC1CON1bits.ICTSEL = 1;               /* Timer2 (000 would be Timer3) */
    IPC0bits.IC1IP = 5;
    IFS0bits.IC1IF = 0;
    IEC0bits.IC1IE = 1;
    IC1CON1bits.ICM = 3;                  /* every rising edge */
    T2CONbits.TON = 1;
}
void __attribute__((interrupt, auto_psv)) _IC1Interrupt(void)
{
    unsigned int now = IC1BUF;
    period = now - last; last = now;
    IFS0bits.IC1IF = 0;
}
""" + MAIN


def codes(code, req=REQ):
    return {f.code for f in check_full(code, {"ic": parse_request(req, 16e6)}, fcy=16e6).findings}


def test_calc():
    lines, d = calc_lines(parse_request(REQ, 16e6))
    assert d["ICxCON1"] == 0x0403 and d["prescale"] == 8
    s = parse_request("capture IC9 falling on RP3 timer3", 16e6)
    assert (s.ic, s.edge, s.timer, s.pin_rp) == (9, "falling", 3, 3)


def test_good():
    rep = check_full(GOOD, {"ic": parse_request(REQ, 16e6)}, fcy=16e6)
    assert rep.ok, rep.errors
    assert not {f.code for f in rep.findings} & {"IC_CON2", "IC_RANGE", "IC_ISR_READ", "IC_ICI"}


def test_rules():
    assert "IC_ICTSEL" in codes(GOOD.replace("ICTSEL = 1", "ICTSEL = 0"))
    assert "IC_ICM" in codes(GOOD.replace("ICM = 3", "ICM = 2"))
    assert "IC_PPS" in codes(GOOD.replace("IC1R = 10", "IC1R = 11"))
    assert "IC_TRIS" in codes(GOOD.replace("TRISF4 = 1", "TRISF4 = 0"))
    assert "IC_TIMER" in codes(GOOD.replace("T2CONbits.TON = 1;", ""))
    assert "IC_RANGE" in codes(GOOD.replace("TCKPS = 1", "TCKPS = 0"))
    assert "IC_CON2" in codes(GOOD.replace(" IC1CON2 = 0;", ""))
    assert "IC_ISR_READ" in codes(GOOD.replace("unsigned int now = IC1BUF;", "unsigned int now = 0;"))
    assert "ISR_FLAG" in codes(GOOD.replace("    IFS0bits.IC1IF = 0;\n}", "}"))


def test_agent(settings):
    m = ScriptedModel([fence(GOOD)])
    r = run_agent(REQ, m, settings, compile_fn=compile_ok)
    assert r.status == "verified", r.attempts[-1].check.errors
    assert r.spec is not None and r.spec.timer == 2 and not r.spec.interrupt
    assert "ICTSEL 000=Timer3" in m.calls[0][1].text


def test_cli(capsys):
    assert cli.main(["calc", "ic", REQ]) == 0
    assert "IC1CON1 = 0x0403" in capsys.readouterr().out
