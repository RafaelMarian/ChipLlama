import re

import pytest

from chipllama.checker import check_timer
from chipllama.cemu import emulate, eval_expr, EvalError
from chipllama.timers import TimerSpec, parse_request
from conftest import GOOD_T1

SPEC = TimerSpec(timer=1, period_s=0.010, fosc_hz=32e6, interrupt=True, priority=5)


def codes(rep, sev="error"):
    return {f.code for f in rep.findings if f.severity == sev}


def test_good_code_passes():
    rep = check_timer(GOOD_T1, SPEC)
    assert rep.ok, rep.feedback_text()
    assert rep.registers["T1CON"] == 0x8010 and rep.registers["PR1"] == 19999
    assert rep.achieved_period_s == pytest.approx(0.010)


def test_emulated_register_values():
    emu = emulate(GOOD_T1)
    assert emu.regs["IEC0"] == 1 << 3 and emu.regs["IPC0"] >> 12 == 5


def test_equivalent_alternative_config_also_passes():
    code = GOOD_T1.replace("PRESCALE 8UL", "PRESCALE 64UL").replace("TCKPS = 1", "TCKPS = 2")
    rep = check_timer(code, SPEC)
    assert rep.ok, rep.feedback_text()      # 2500 ticks * 64 = 10 ms: different but valid


def test_wrong_prescaler_detected():
    rep = check_timer(GOOD_T1.replace("TCKPS = 1", "TCKPS = 2"), SPEC)
    assert "PERIOD" in codes(rep)
    msg = next(f.message for f in rep.errors if f.code == "PERIOD")
    assert "TCKPS=1" in msg and "PR=19999" in msg      # feedback tells the model the right answer


def test_wrong_fosc_vs_fcy_detected():
    # classic mistake: forgetting Fcy = Fosc/2
    rep = check_timer(GOOD_T1.replace("#define FCY  (FOSC / 2UL)", "#define FCY  FOSC"), SPEC)
    assert "PERIOD" in codes(rep) and "CLOCK_MACRO" in codes(rep, "warning")


@pytest.mark.parametrize("mutation,code", [
    (("T1CONbits.TON = 1;", ""), "TON"),
    (("T1CONbits.TON = 1;", "T1CONbits.TCS = 1; T1CONbits.TON = 1;"), "TCS"),
    (("T1CONbits.TON = 1;", "T1CONbits.TGATE = 1; T1CONbits.TON = 1;"), "TGATE"),
    (("IEC0bits.T1IE = 1;", ""), "IE"),
    (("IPC0bits.T1IP = 5;", "IPC0bits.T1IP = 0;"), "IP"),
    (("IPC0bits.T1IP = 5;", "IPC0bits.T1IP = 3;"), "IP"),
])
def test_register_mistakes(mutation, code):
    rep = check_timer(GOOD_T1.replace(*mutation), SPEC)
    assert code in codes(rep), rep.feedback_text()


def test_isr_problems():
    no_clear = GOOD_T1.replace("    IFS0bits.T1IF = 0;\n}", "}")
    assert "ISR_FLAG" in codes(check_timer(no_clear, SPEC))
    wrong_name = GOOD_T1.replace("_T1Interrupt", "_T2Interrupt")
    assert "ISR" in codes(check_timer(wrong_name, SPEC))
    no_attr = GOOD_T1.replace("__attribute__((interrupt, auto_psv)) ", "")
    assert "ISR_ATTR" in codes(check_timer(no_attr, SPEC))


def test_isr_clearing_flag_via_alias_and_mask():
    alias = GOOD_T1.replace("IFS0bits.T1IF = 0;\n}", "_T1IF = 0;\n}")
    assert check_timer(alias, SPEC).ok
    mask = GOOD_T1.replace("IFS0bits.T1IF = 0;\n}", "IFS0 &= ~(1 << 3);\n}")
    assert check_timer(mask, SPEC).ok


def test_direct_register_writes_and_bit_math():
    code = """#include <xc.h>
void init(void){ PR1 = 19999; T1CON = 0x8010; _T1IP = 5; _T1IE = 1; }
void __attribute__((interrupt, auto_psv)) _T1Interrupt(void){ _T1IF = 0; }
int main(void){ init(); for(;;); }"""
    rep = check_timer(code, SPEC)
    assert rep.ok, rep.feedback_text()
    # PR loaded before TON via the single T1CON write; but writing PR *after* TON warns
    late = code.replace("PR1 = 19999; T1CON = 0x8010;", "T1CON = 0x8010; PR1 = 19999;")
    assert "ORDER" in codes(check_timer(late, SPEC), "warning")


def test_tckps_single_bit_aliases_and_or_assign():
    code = GOOD_T1.replace("T1CONbits.TCKPS = 1;", "T1CONbits.TCKPS0 = 1; T1CONbits.TCKPS1 = 0;")
    assert check_timer(code, SPEC).ok
    code = GOOD_T1.replace("T1CONbits.TCKPS = 1;", "T1CON |= (1 << 4);")
    assert check_timer(code, SPEC).ok


def test_pr1_unset_and_unimplemented_bits():
    code = GOOD_T1.replace("    PR1 = (FCY / 1000UL * PERIOD_MS / PRESCALE) - 1UL;\n", "")
    rep = check_timer(code, SPEC)
    assert {"PERIOD", "PR_UNSET"} <= codes(rep)
    assert "UNIMPL" in codes(check_timer(GOOD_T1.replace("T1CON = 0;", "T1CON = 0xFFFF;"), SPEC), "warning")


def test_polling_mode_without_interrupt():
    spec = TimerSpec(timer=1, period_s=0.010, fosc_hz=32e6, interrupt=False)
    code = """#include <xc.h>
void init(void){ PR1 = 19999; T1CON = 0x8010; }
int main(void){ init(); for(;;){ if (IFS0bits.T1IF) { IFS0bits.T1IF = 0; } } }"""
    assert check_timer(code, spec).ok


def test_timer2_32bit():
    spec = parse_request("Timer2/3 32-bit 5 s period at 32MHz")
    code = """#include <xc.h>
void init(void){ T2CON = 0; T3CON = 0; TMR3 = 0; TMR2 = 0;
 PR3 = 1220; PR2 = 46079;   /* 80,000,000 ticks - 1 = (1220 << 16) + 46079 */
 T2CONbits.T32 = 1; _T3IP = 4; _T3IF = 0; _T3IE = 1; T2CONbits.TON = 1; }
void __attribute__((interrupt, auto_psv)) _T3Interrupt(void){ _T3IF = 0; }
int main(void){ init(); for(;;); }"""
    rep = check_timer(code, spec)
    assert rep.ok, rep.feedback_text()
    assert "T32" in codes(check_timer(code.replace("T2CONbits.T32 = 1;", ""), spec))


def test_timer3_alone_and_t32_conflict():
    spec = TimerSpec(timer=3, period_s=0.001, fosc_hz=32e6, interrupt=False)
    code = "#include <xc.h>\nvoid i(void){ PR3 = 15999; T3CON = 0x8000; }\nint main(void){i();for(;;);}"
    assert check_timer(code, spec).ok
    assert "T32" in codes(check_timer(code.replace("PR3 = 15999;", "T2CONbits.T32 = 1; PR3 = 15999;"), spec))


def test_impossible_spec_reports_spec_error():
    rep = check_timer(GOOD_T1, TimerSpec(timer=1, period_s=60.0))
    assert "SPEC" in codes(rep)


def test_expression_evaluator():
    n = lambda _: None
    assert eval_expr("(uint16_t)(16000000UL/1000UL*10UL/8UL - 1)", n) == 19999
    assert eval_expr("0x8000 | (1 << 4)", n) == 0x8010
    assert eval_expr("~0x00FF & 0xFFFF", n) == 0xFF00
    assert eval_expr("7/2", n) == 3 and eval_expr("0.5*4", n) == 2.0
    with pytest.raises(EvalError):
        eval_expr("foo(3)", n)
    with pytest.raises(EvalError):
        eval_expr("__import__('os')", n)
