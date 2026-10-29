import pytest
from conftest import CFG, ScriptedModel, compile_ok, fence

from chipllama import cli
from chipllama.agent import run_agent
from chipllama.peripherals import check_full
from chipllama.pwm import parse_request, solve

MAIN = "int main(void) { CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; pwm_init(); while (1) { } }\n"

REQ_SELF = "PWM 20 kHz 25% duty on RP2 using OC1"
GOOD_SELF = CFG + """
static void pwm_init(void)
{
    __builtin_write_OSCCONL(OSCCON & 0xBF);
    RPOR1bits.RP2R = 18;            /* OC1 -> RP2 (RD8) */
    __builtin_write_OSCCONL(OSCCON | 0x40);
    TRISDbits.TRISD8 = 0;
    OC1CON1 = 0; OC1CON2 = 0;
    OC1RS = 799;                    /* 16 MHz / 800 = 20 kHz */
    OC1R = 200;                     /* 25 % */
    OC1CON2 = 0x001F;               /* SYNCSEL = self */
    OC1CON1 = 0x1C06;               /* OCTSEL = Fcy, OCM = edge PWM */
}
""" + MAIN

REQ_SERVO = "50 Hz servo PWM 7.5% on RD8 with OC3"
GOOD_SERVO = CFG + """
static void pwm_init(void)
{
    __builtin_write_OSCCONL(OSCCON & 0xBF);
    RPOR1bits.RP2R = 20;            /* OC3 -> RP2 */
    __builtin_write_OSCCONL(OSCCON | 0x40);
    TRISDbits.TRISD8 = 0;
    T2CON = 0; TMR2 = 0;
    T2CONbits.TCKPS = 1;            /* 1:8 */
    PR2 = 39999;                    /* 20 ms */
    OC3CON1 = 0; OC3CON2 = 0;
    OC3R = 3000;                    /* 1.5 ms */
    OC3CON2bits.SYNCSEL = 0x0C;     /* Timer2 */
    OC3CON1bits.OCTSEL = 0;         /* Timer2 clock */
    OC3CON1bits.OCM = 6;
    T2CONbits.TON = 1;
}
""" + MAIN


def _codes(code, req):
    spec = parse_request(req)
    return {f.code for f in check_full(code, {"pwm": spec}, fcy=16e6).findings}, spec


def test_calc():
    s = solve(parse_request(REQ_SELF))
    assert (s["OCxRS"], s["OCxR"], s["mode"]) == (799, 200, "self")
    s = solve(parse_request(REQ_SERVO))
    assert (s["mode"], s["timer"], s["prescale"], s["ticks"], s["OCxR"]) == ("timer", 2, 8, 40000, 3000)


@pytest.mark.parametrize("code,req", [(GOOD_SELF, REQ_SELF), (GOOD_SERVO, REQ_SERVO)])
def test_good(code, req):
    rep = check_full(code, {"pwm": parse_request(req)}, fcy=16e6)
    assert rep.ok, rep.errors
    assert not {f.code for f in rep.findings} & {"PWM_CON2", "PWM_TRIS", "PPS_UNLOCK"}


def test_rules():
    c, _ = _codes(GOOD_SELF.replace("OC1RS = 799", "OC1RS = 999"), REQ_SELF)
    assert {"PWM_FREQ", "PWM_DUTY"} <= c
    c, _ = _codes(GOOD_SELF.replace("RPOR1bits.RP2R = 18", "RPOR1bits.RP2R = 19"), REQ_SELF)
    assert "PWM_PPS" in c
    c, _ = _codes(GOOD_SELF.replace(" OC1CON2 = 0;", "").replace("OC1CON2 = 0x001F;", ""), REQ_SELF)
    assert "PWM_CON2" in c
    c, _ = _codes(GOOD_SELF.replace("0x1C06", "0x1C05"), REQ_SELF)
    assert "PWM_OCM" in c
    c, _ = _codes(GOOD_SERVO.replace("T2CONbits.TON = 1;", ""), REQ_SERVO)
    assert "PWM_TIMER" in c


def test_agent(settings):
    m = ScriptedModel([fence(GOOD_SERVO)])
    r = run_agent(REQ_SERVO, m, settings, compile_fn=compile_ok)
    assert r.status == "verified", r.attempts[-1].check.errors
    assert r.spec is not None and r.spec.timer == 2 and not r.spec.interrupt
    assert "OCxCON2 resets" in m.calls[0][1].text
    m = ScriptedModel([fence(GOOD_SELF)])
    r = run_agent(REQ_SELF, m, settings, compile_fn=compile_ok)
    assert r.status == "verified" and r.spec is None, r.attempts[-1].check.errors


def test_cli(capsys):
    assert cli.main(["calc", "pwm", REQ_SELF]) == 0
    assert "OC1RS = 799" in capsys.readouterr().out
