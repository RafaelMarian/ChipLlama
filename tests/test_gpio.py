from conftest import CFG, ScriptedModel, compile_ok, fence

from chipllama import cli
from chipllama.agent import run_agent
from chipllama.cemu import emulate
from chipllama.checker import CheckReport
from chipllama.gpio import parse_request, check, calc_lines
from chipllama.peripherals import check_full

REQ = "RA0 output (LED), RD6 input button with pull-up and change notification interrupt on RD6, priority 4"

GOOD = CFG + """
#define FCY 16000000UL
volatile unsigned int presses;
static void gpio_init(void)
{
    LATAbits.LATA0 = 0;
    TRISAbits.TRISA0 = 0;          /* RA0 output */
    TRISDbits.TRISD6 = 1;          /* RD6 input  */
    CNPU1bits.CN15PUE = 1;         /* RD6 = CN15 pull-up */
    CNEN1bits.CN15IE = 1;
    IPC4bits.CNIP = 4;
    IFS1bits.CNIF = 0;
    IEC1bits.CNIE = 1;
}
void __attribute__((interrupt, auto_psv)) _CNInterrupt(void)
{
    if (!PORTDbits.RD6) { presses++; LATAbits.LATA0 ^= 1; }
    IFS1bits.CNIF = 0;
}
int main(void) { CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; gpio_init(); while (1) { } }
"""


def codes(code, spec):
    return {f.code for f in check_full(code, spec and {"gpio": spec}, fcy=16e6).findings if f.severity == "error"}


def test_parse():
    s = parse_request(REQ)
    d = {p.name: p for p in s.pins}
    assert d["RA0"].output and not d["RA0"].cn
    assert not d["RD6"].output and d["RD6"].pullup and d["RD6"].cn and d["RD6"].cn_num == 15
    assert s.cn_priority == 4
    assert parse_request("UART1 115200 baud TX on RF5") is None
    assert parse_request("open-drain output on RG2").pins[0].od


def test_calc_lines():
    lines, d = calc_lines(parse_request(REQ))
    txt = "\n".join(lines)
    assert "TRISAbits.TRISA0 = 0" in txt and "CNPU1bits.CN15PUE = 1" in txt and "_CNInterrupt" in txt
    assert d["cn_vector"]["ipc"] == ("IPC4", "CNIP")


def test_good_sample_passes():
    rep = check_full(GOOD, {"gpio": parse_request(REQ)}, fcy=16e6)
    assert rep.ok, rep.errors


def test_rules():
    s = parse_request(REQ)
    assert "GPIO_TRIS" in codes(GOOD.replace("TRISAbits.TRISA0 = 0;", ""), s)
    assert "GPIO_PU" in codes(GOOD.replace("CNPU1bits.CN15PUE = 1;", ""), s)
    assert "GPIO_CNEN" in codes(GOOD.replace("CNEN1bits.CN15IE = 1;", "CNEN1bits.CN14IE = 1;"), s)
    assert "IP" in codes(GOOD.replace("IPC4bits.CNIP = 4;", "IPC4bits.CNIP = 0;"), s)
    assert "IP" in codes(GOOD.replace("IPC4bits.CNIP = 4;", "IPC4bits.CNIP = 6;"), s)
    assert "ISR_FLAG" in codes(GOOD.replace("IFS1bits.CNIF = 0;\n}", "}"), s)
    assert "ISR" in codes(GOOD.replace("_CNInterrupt", "_ChangeInterrupt"), s)


def test_analog_input_and_missing_pin():
    s = parse_request("read button on RB5", force=True)
    good = CFG + "void f(void){ AD1PCFGLbits.PCFG5 = 1; TRISBbits.TRISB5 = 1; x = PORTBbits.RB5; }\n"
    rep = CheckReport(); check(good, s, rep, emulate(good)); assert rep.ok, rep.errors
    bad = good.replace("AD1PCFGLbits.PCFG5 = 1;", "")
    rep = CheckReport(); check(bad, s, rep, emulate(bad))
    assert "GPIO_ANALOG" in {f.code for f in rep.errors}
    s = parse_request("LED on RA8", force=True)
    rep = CheckReport(); check("", s, rep, emulate(""))
    assert "GPIO_PIN" in {f.code for f in rep.errors}


def test_pps_override_and_pull_up_on_non_cn_pin():
    s = parse_request("LED output on RB7", force=True)             # RB7 = RP7
    code = "void f(void){ TRISBbits.TRISB7 = 0; RPOR3bits.RP7R = 3; LATBbits.LATB7 = 1; }\n"
    rep = CheckReport(); check(code, s, rep, emulate(code))
    assert "GPIO_PPS" in {f.code for f in rep.errors}
    s = parse_request("RG2 input with pull-up", force=True)
    rep = CheckReport(); check("", s, rep, emulate(""))
    assert "GPIO_NO_PU" in {f.code for f in rep.errors}


def test_agent_gpio_only(settings):
    m = ScriptedModel([fence(GOOD)])
    r = run_agent(REQ, m, settings, compile_fn=compile_ok)
    assert r.status == "verified", (r.error, r.attempts and r.attempts[-1].check.errors)
    assert r.spec is None and "gpio" in r.to_dict()["peripherals"]
    assert "CN15PUE" in m.calls[0][1].text


def test_agent_gpio_feedback(settings):
    bad = GOOD.replace("CNPU1bits.CN15PUE = 1;", "")
    m = ScriptedModel([fence(bad), fence(GOOD)])
    r = run_agent(REQ, m, settings, compile_fn=compile_ok)
    assert r.status == "verified" and len(r.attempts) == 2
    assert "GPIO_PU" in m.calls[1][-1].text and "p.135" in m.calls[1][-1].text


def test_cli(capsys):
    assert cli.main(["calc", "gpio", "RD6 input with pull-up"]) == 0
    assert "CN15PUE" in capsys.readouterr().out


def test_blink_with_period_routes_to_timer_plus_gpio(settings):
    code = CFG + """
void init(void){ TRISAbits.TRISA0 = 0; T1CON = 0; TMR1 = 0; PR1 = 31249; T1CONbits.TCKPS = 3;
  IPC0bits.T1IP = 4; IFS0bits.T1IF = 0; IEC0bits.T1IE = 1; T1CONbits.TON = 1; }
void __attribute__((interrupt, auto_psv)) _T1Interrupt(void){ LATAbits.LATA0 ^= 1; IFS0bits.T1IF = 0; }
int main(void){ CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; init(); while(1){} }
"""
    m = ScriptedModel([fence(code)])
    r = run_agent("Blink an LED on RA0 every 500 ms using Timer1 interrupt", m, settings, compile_fn=compile_ok)
    assert r.spec is not None and r.spec.timer == 1 and "gpio" in r.peripherals
    assert r.status == "verified", r.attempts[-1].check.errors
    bad = code.replace("TRISAbits.TRISA0 = 0;", "")
    m = ScriptedModel([fence(bad), fence(code)])
    r = run_agent("Blink an LED on RA0 every 500 ms using Timer1 interrupt", m, settings, compile_fn=compile_ok)
    assert "GPIO_TRIS" in m.calls[1][-1].text
