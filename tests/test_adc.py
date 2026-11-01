import pytest
from conftest import CFG, ScriptedModel, compile_ok, fence

from chipllama import cli
from chipllama.adc import parse_request, solve, min_adcs
from chipllama.agent import run_agent
from chipllama.peripherals import check_full

MAIN = "int main(void) { CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; adc_init(); while (1) { } }\n"

REQ_AUTO = "Sample AN5 continuously at 10 kHz with ADC interrupt priority 3"
GOOD_AUTO = CFG + """
volatile unsigned int last;
static void adc_init(void)
{
    TRISBbits.TRISB5 = 1;
    AD1PCFGL = 0xFFFF;
    AD1PCFGLbits.PCFG5 = 0;          /* AN5 analog */
    AD1CON1 = 0;
    AD1CON1bits.SSRC = 7;            /* internal counter */
    AD1CON1bits.ASAM = 1;
    AD1CON2 = 0;
    AD1CON3bits.ADCS = 39;           /* TAD = 40 * 62.5 ns = 2.5 us */
    AD1CON3bits.SAMC = 28;           /* (28 + 12) * 2.5 us = 100 us -> 10 kHz */
    AD1CHS = 5;
    IPC3bits.AD1IP = 3;
    IFS0bits.AD1IF = 0;
    IEC0bits.AD1IE = 1;
    AD1CON1bits.ADON = 1;
}
void __attribute__((interrupt, auto_psv)) _ADC1Interrupt(void) { last = ADC1BUF0; IFS0bits.AD1IF = 0; }
""" + MAIN

REQ_SCAN = "Scan AN0, AN1 and AN4 triggered by Timer3 at 1 kHz"
GOOD_SCAN = CFG + """
volatile unsigned int v[3];
static void adc_init(void)
{
    TRISB |= 0x0013;
    AD1PCFGL = 0xFFEC;
    AD1CON1 = 0x0044;                /* SSRC=010 Timer3, ASAM=1 */
    AD1CON2 = 0x0408;                /* CSCNA=1, SMPI=2 */
    AD1CON3 = 0x0F01;                /* SAMC=15, ADCS=1 -> TAD 125 ns */
    AD1CSSL = 0x0013;
    T3CON = 0; TMR3 = 0; PR3 = 15999;   /* 1 ms at 1:1 */
    T3CONbits.TON = 1;
    AD1CON1bits.ADON = 1;
}
void poll(void) { if (AD1CON1bits.DONE) { v[0] = ADC1BUF0; v[1] = ADC1BUF1; v[2] = ADC1BUF2; } }
""" + MAIN

REQ_MAN = "Read potentiometer on AN2"
GOOD_MAN = CFG + """
static void adc_init(void)
{
    AD1PCFGLbits.PCFG2 = 0; TRISBbits.TRISB2 = 1;
    AD1CON1 = 0x00E0; AD1CON2 = 0; AD1CON3 = 0x0F01; AD1CHS = 2;
    AD1CON1bits.ADON = 1;
}
unsigned int read(void) { AD1CON1bits.SAMP = 1; while (!AD1CON1bits.DONE); return ADC1BUF0; }
""" + MAIN


def errs(code, req):
    return {f.code for f in check_full(code, {"adc": parse_request(req, 16e6)}, fcy=16e6).errors}


def test_calc():
    assert min_adcs(16e6) == 1 and min_adcs(8e6) == 0 and min_adcs(4e6) == 0
    s = solve(parse_request(REQ_AUTO, 16e6))
    assert abs(s.rate_hz - 10e3) / 10e3 < 1e-9 and s.tad >= 75e-9
    s = solve(parse_request(REQ_SCAN, 16e6))
    assert s.registers["AD1CSSL"] == 0x13 and s.smpi == 2 and s.registers["AD1CON1"] == 0x8044
    with pytest.raises(ValueError):
        solve(parse_request("ADC sample AN0 continuously at 1 Hz", 16e6))


@pytest.mark.parametrize("code,req", [(GOOD_AUTO, REQ_AUTO), (GOOD_SCAN, REQ_SCAN), (GOOD_MAN, REQ_MAN)])
def test_good_samples(code, req):
    rep = check_full(code, {"adc": parse_request(req, 16e6)}, fcy=16e6)
    assert rep.ok, rep.errors


def test_rules():
    assert "ADC_TAD" in errs(GOOD_MAN.replace("AD1CON3 = 0x0F01", "AD1CON3 = 0x0F00"), REQ_MAN)
    assert "ADC_PCFG" in errs(GOOD_MAN.replace("AD1PCFGLbits.PCFG2 = 0;", "AD1PCFGLbits.PCFG2 = 1;"), REQ_MAN)
    assert "ADC_CH" in errs(GOOD_MAN.replace("AD1CHS = 2", "AD1CHS = 3"), REQ_MAN)
    assert "ADC_ADON" in errs(GOOD_MAN.replace("AD1CON1bits.ADON = 1;", ""), REQ_MAN)
    assert "ADC_SAMC" in errs(GOOD_MAN.replace("AD1CON3 = 0x0F01", "AD1CON3 = 0x0001"), REQ_MAN)
    assert "ADC_RATE" in errs(GOOD_AUTO.replace("SAMC = 28", "SAMC = 20"), REQ_AUTO)
    assert "ADC_SSRC" in errs(GOOD_AUTO.replace("SSRC = 7", "SSRC = 2"), REQ_AUTO)
    assert "ISR_FLAG" in errs(GOOD_AUTO.replace(" IFS0bits.AD1IF = 0; }", " }"), REQ_AUTO)
    assert "ADC_CSSL" in errs(GOOD_SCAN.replace("AD1CSSL = 0x0013", "AD1CSSL = 0x0003"), REQ_SCAN)
    assert "ADC_SMPI" in errs(GOOD_SCAN.replace("AD1CON2 = 0x0408", "AD1CON2 = 0x0400"), REQ_SCAN)
    assert "ADC_SCAN" in errs(GOOD_SCAN.replace("AD1CON2 = 0x0408", "AD1CON2 = 0x0008"), REQ_SCAN)


def test_order_warning():
    late = GOOD_MAN.replace("AD1CON1bits.ADON = 1;", "AD1CON1bits.ADON = 1; AD1CON3 = 0x0F01;")
    rep = check_full(late, {"adc": parse_request(REQ_MAN, 16e6)}, fcy=16e6)
    assert "ADC_ORDER" in {f.code for f in rep.findings}


def test_agent_timer3_trigger_checks_timer_and_adc(settings):
    m = ScriptedModel([fence(GOOD_SCAN)])
    r = run_agent(REQ_SCAN, m, settings, compile_fn=compile_ok)
    assert r.spec is not None and r.spec.timer == 3 and not r.spec.interrupt
    assert r.status == "verified", r.attempts[-1].check.errors
    bad = GOOD_SCAN.replace("PR3 = 15999", "PR3 = 7999")
    m = ScriptedModel([fence(bad), fence(GOOD_SCAN)])
    r = run_agent(REQ_SCAN, m, settings, compile_fn=compile_ok)
    assert "PERIOD" in {f.code for f in r.attempts[0].check.errors} and r.status == "verified"


def test_agent_adc_only(settings):
    m = ScriptedModel([fence(GOOD_AUTO)])
    r = run_agent(REQ_AUTO, m, settings, compile_fn=compile_ok)
    assert r.status == "verified", r.attempts[-1].check.errors
    assert r.spec is None and "TAD" in m.calls[0][1].text


def test_cli(capsys):
    assert cli.main(["calc", "adc", "AN3 at 50 kHz"]) == 0
    out = capsys.readouterr().out
    assert "AD1CON3" in out and "samples/s" in out
