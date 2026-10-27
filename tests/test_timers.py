import pytest

from chipllama.timers import TimerSpec, parse_request, solve, timer_period_s


def test_t1_10ms_at_32mhz():
    s = solve(TimerSpec(timer=1, period_s=0.010, fosc_hz=32e6))
    assert (s.tckps, s.prescale, s.period_reg) == (1, 8, 19999)
    assert s.con_value == 0x8010 and s.registers == {"T1CON": 0x8010, "PR1": 19999}
    assert s.achieved_s == pytest.approx(0.010)


def test_fcy_is_fosc_over_two():
    assert TimerSpec(fosc_hz=8e6).fcy == 4e6
    s = solve(TimerSpec(timer=1, period_s=0.001, fosc_hz=8e6))   # Fcy=4 MHz -> 4000 ticks
    assert (s.prescale, s.period_reg) == (1, 3999)


def test_smallest_prescaler_that_fits():
    s = solve(TimerSpec(timer=1, period_s=0.001, fosc_hz=32e6))  # 16000 ticks fits 1:1
    assert s.prescale == 1 and s.period_reg == 15999
    s = solve(TimerSpec(timer=1, period_s=0.1, fosc_hz=32e6))    # 1.6M ticks -> 1:64 = 25000
    assert s.prescale == 64 and s.period_reg == 24999
    s = solve(TimerSpec(timer=2, period_s=1.0, fosc_hz=32e6))    # 16M ticks -> 1:256 = 62500
    assert s.prescale == 256 and s.period_reg == 62499 and s.registers["T2CON"] == (1 << 15) | (3 << 4)


def test_too_long_for_16bit_and_32bit_mode():
    with pytest.raises(ValueError, match="32-bit"):
        solve(TimerSpec(timer=2, period_s=10.0, fosc_hz=32e6))
    s = solve(TimerSpec(timer=2, period_s=10.0, fosc_hz=32e6, bits32=True))
    assert s.registers["T2CON"] & (1 << 3)               # T32
    pr = (s.registers["PR3"] << 16) | s.registers["PR2"]  # PR3 = MSW, PR2 = LSW
    assert pr == 160_000_000 - 1
    assert timer_period_s(16e6, 1, pr) == pytest.approx(10.0)


def test_timer1_has_no_32bit_mode():
    with pytest.raises(ValueError):
        solve(TimerSpec(timer=1, period_s=1, bits32=True))


@pytest.mark.parametrize("text,timer,period,fosc,irq,prio", [
    ("Configure Timer1 to interrupt every 10 ms at 32 MHz", 1, 0.010, 32e6, True, None),
    ("Timer1 1 kHz interrupt, Fosc = 8 MHz, priority 5", 1, 0.001, 8e6, True, 5),
    ("Toggle an LED every 500 ms using Timer2 with a 4 MIPS clock", 2, 0.5, 8e6, True, None),
    ("Timer 1 for a 250 us tick, 8 MHz FRC, no interrupt", 1, 250e-6, 8e6, False, None),
    ("Fcy = 16 MHz, timer3 period of 2 s", 3, 2.0, 32e6, True, None),
])
def test_parse_request(text, timer, period, fosc, irq, prio):
    sp = parse_request(text)
    assert (sp.timer, sp.interrupt, sp.priority) == (timer, irq, prio)
    assert sp.period_s == pytest.approx(period) and sp.fosc_hz == fosc


def test_parse_32bit():
    sp = parse_request("Timer2/3 32-bit 5 s period at 32MHz")
    assert sp.bits32 and sp.period_s == 5.0
