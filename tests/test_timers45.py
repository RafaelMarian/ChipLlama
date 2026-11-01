"""Timers 1-5: Timer4/5, 32-bit Timer4/5 pair, gated time accumulation."""
from conftest import CFG

from chipllama.checker import check_timer
from chipllama.timers import parse_request, solve, interrupt_names
from chipllama import cemu

CLK = "int main(void) { CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; init(); while (1) { } }\n"

GOOD_T4 = CFG + """
#define FCY 16000000UL
void init(void)
{
    T4CON = 0;
    TMR4 = 0;
    PR4 = (FCY / 1000UL) - 1UL;        /* 1 ms at 1:1 */
    IPC6bits.T4IP = 3;
    IFS1bits.T4IF = 0;
    IEC1bits.T4IE = 1;
    T4CONbits.TON = 1;
}
void __attribute__((interrupt, auto_psv)) _T4Interrupt(void) { _T4IF = 0; LATAbits.LATA0 ^= 1; }
""" + CLK

GOOD_T45 = CFG + """
void init(void)
{
    T4CON = 0; T5CON = 0;
    T4CONbits.T32 = 1;
    TMR5 = 0; TMR4 = 0;
    PR5 = 0x0989;  PR4 = 0x67FF;      /* 159999999 -> 10 s at 16 MHz, 1:1 */
    IPC7bits.T5IP = 2;
    IFS1bits.T5IF = 0;
    IEC1bits.T5IE = 1;
    T4CONbits.TON = 1;
}
void __attribute__((interrupt, no_auto_psv)) _T5Interrupt(void) { IFS1bits.T5IF = 0; }
""" + CLK

GOOD_GATED = CFG + """
volatile unsigned int width;
void init(void)
{
    AD1PCFGLbits.PCFG7 = 1;           /* RB7 = RP7 digital */
    TRISBbits.TRISB7 = 1;
    __builtin_write_OSCCONL(OSCCON & 0xBF);
    RPINR2bits.T1CKR = 7;             /* T1CK gate <- RP7 */
    __builtin_write_OSCCONL(OSCCON | 0x40);
    T1CON = 0;
    TMR1 = 0;
    PR1 = 39999;                      /* 20 ms range at 1:8 */
    T1CONbits.TCKPS = 1;
    T1CONbits.TGATE = 1;
    IPC0bits.T1IP = 4;
    IFS0bits.T1IF = 0;
    IEC0bits.T1IE = 1;
    T1CONbits.TON = 1;
}
void __attribute__((interrupt, auto_psv)) _T1Interrupt(void) { width = TMR1; TMR1 = 0; IFS0bits.T1IF = 0; }
""" + CLK


def test_parse_timer45_and_32bit_pair():
    s = parse_request("Timer4 interrupt every 1 ms")
    assert (s.timer, s.bits32, interrupt_names(s)["isr"]) == (4, False, "_T4Interrupt")
    s = parse_request("32-bit Timer4/5 interrupt every 10 s")
    assert (s.base, s.bits32, interrupt_names(s)["isr"]) == (4, True, "_T5Interrupt")
    sol = solve(s)
    assert sol.registers == {"T4CON": 0x8008, "PR4": 0x67FF, "PR5": 0x0989}
    s = parse_request("timer 5 polling 100 us")
    assert (s.timer, s.interrupt) == (5, False)


def test_good_timer4_passes():
    rep = check_timer(GOOD_T4, parse_request("Timer4 interrupt every 1 ms priority 3"))
    assert rep.ok, rep.errors


def test_good_timer45_passes_and_t32_missing_fails():
    spec = parse_request("32-bit Timer4/5 interrupt every 10 s")
    assert check_timer(GOOD_T45, spec).ok
    bad = GOOD_T45.replace("T4CONbits.T32 = 1;", "")
    assert "T32" in {f.code for f in check_timer(bad, spec).errors}
    bad = GOOD_T45.replace("_T5Interrupt", "_T4Interrupt")
    assert "ISR" in {f.code for f in check_timer(bad, spec).errors}


def test_gated_parse_and_good_sample():
    s = parse_request("Timer1 gated mode: measure pulse width up to 20 ms, gate on RP7")
    assert s.gated and s.gate_rp == 7 and abs(s.period_s - 0.02) < 1e-12
    sol = solve(s)
    assert sol.registers["T1CON"] & (1 << 6)
    rep = check_timer(GOOD_GATED, s)
    assert rep.ok, rep.errors


def test_gated_rules():
    s = parse_request("Timer1 gated mode: measure pulse width up to 20 ms, gate on RP7")
    codes = lambda c: {f.code for f in check_timer(c, s).errors}
    assert "TGATE" in codes(GOOD_GATED.replace("T1CONbits.TGATE = 1;", ""))
    assert "GATE_PPS" in codes(GOOD_GATED.replace("RPINR2bits.T1CKR = 7;", "RPINR2bits.T1CKR = 8;"))
    assert "GATE_ANALOG" in codes(GOOD_GATED.replace("AD1PCFGLbits.PCFG7 = 1;", ""))
    assert "PERIOD" in codes(GOOD_GATED.replace("PR1 = 39999;", "PR1 = 9999;"))
    # a longer range than needed is fine for gated accumulation
    assert check_timer(GOOD_GATED.replace("PR1 = 39999;", "PR1 = 0xFFFF;"), s).ok
    # non-gated request still rejects TGATE
    assert "TGATE" in {f.code for f in check_timer(GOOD_GATED, parse_request("Timer1 interrupt every 20 ms")).errors}


def test_cemu_generic_bit_aliases_and_isr_skip():
    emu = cemu.emulate(GOOD_T4)
    assert emu.get("IFS1", "T4IF") == 0 and emu.get("IEC1", "T4IE") == 1
    assert "LATA" not in emu.unknown_targets      # ports are now modelled


def test_agent_timer4_and_gated(settings):
    from conftest import ScriptedModel, compile_ok, fence
    from chipllama.agent import run_agent
    m = ScriptedModel([fence(GOOD_T4)])
    r = run_agent("Timer4 interrupt every 1 ms priority 3", m, settings, compile_fn=compile_ok)
    assert r.status == "verified", r.error
    assert "T4CON" in m.calls[0][1].text
    m = ScriptedModel([fence(GOOD_GATED)])
    r = run_agent("Timer1 gated mode: measure pulse width up to 20 ms, gate on RP7", m, settings, compile_fn=compile_ok)
    assert r.status == "verified", r.error
    assert "TGATE" in m.calls[0][1].text


def test_cli_calc_timer45(capsys):
    from chipllama import cli
    assert cli.main(["calc", "timer", "32-bit Timer4/5 every 10 s"]) == 0
    out = capsys.readouterr().out
    assert "PR5" in out and "_T5Interrupt" in out
    assert cli.main(["calc", "Timer1", "10", "ms"]) == 0
