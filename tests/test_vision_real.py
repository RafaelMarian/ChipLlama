"""Regression tests replaying vision extractions of 3 REAL web images (examples/debug_web, see SOURCES.md). No network."""
import json
from pathlib import Path

from chipllama import chipdb
from chipllama.vision_debug import format_report, normalize, parse_intent, vision_debug

FX = Path(__file__).parent / "fixtures"
V = FX / "vision_real"


def ex(name):
    return normalize(json.loads((V / name).read_text()))


def test_intent_parses_clock_pins_vdd_writes():
    it = parse_intent("UART1 9600 baud at Fcy 4 MHz, RX on RP4, 3.3V logic")
    assert it.fcy_hz == 4e6 and it.baud == 9600 and it.rx_pin == 4 and it.vdd == 3.3 and "uart" in it.topics
    assert parse_intent("8 MHz crystal, UART 9600").fcy_hz == 4e6
    assert parse_intent("Fosc = 32 MHz timer1 10 ms").fcy_hz == 16e6
    w = parse_intent("Write LATA = 0xBBAA and read it back")
    assert w.writes == {"LATA": 0xBBAA} and w.topics == {"port"}


def test_scope_contention_mid_level_is_bug_not_ok():
    rep = vision_debug([], "PIC24 UART1 RX on RP4 receiving 9600 baud from MSP430, 3.3V logic", extraction=ex("05_scope_contention.json"))
    assert rep.verdict.startswith("BUG FOUND"), rep.verdict
    assert "Invalid logic level" in rep.root_cause and "1.92" in rep.root_cause
    assert any("PPS direction mismatch" in c and "RPOR2.RP4R = 3" in c for c in rep.analysis.causes)
    assert any(f.c_line == "RPOR2bits.RP4R = 0;" for f in rep.analysis.fixes)
    assert any(f.c_line == "RPINR18bits.U1RXR = 4;" for f in rep.analysis.fixes)


def test_scope_contention_confirms_user_code():
    code = "int main(void){ RPOR2bits.RP4R = 3; RPINR18bits.U1RXR = 3; U1BRG = 25; while(1); }"
    rep = vision_debug([], "UART1 RX on RP4 9600 baud 3.3V", code=code, extraction=ex("05_scope_contention.json"))
    assert any("Your code does exactly this: RP4R = 3" in c for c in rep.analysis.causes)
    assert "RP4R = 0" in rep.fix_diff


def test_scope_good_levels_ok():
    e = normalize({"kind": "scope", "scope": {"v_high": 3.28, "v_low": 0.05}})
    rep = vision_debug([], "UART1 9600 baud 3.3V", extraction=e)
    assert not rep.verdict.startswith("BUG")


def test_uart_rx_empty_uses_intent_fcy_and_decodes_u1sta():
    rep = vision_debug([], "UART1 9600 baud at Fcy 4 MHz, loopback: send 'a' and read U1RXREG", extraction=ex("01_mplabx_uart_rx_empty.json"))
    brg = next(r for r in rep.analysis.rows if r.register == "U1BRG")
    assert brg.status == "ok" and "9,615" in brg.decoded and "Fcy 4 MHz" in brg.decoded
    assert "38,462" not in rep.verdict
    sta = next(r for r in rep.analysis.rows if r.register == "U1STA")
    assert "URXDA=0" in sta.decoded and "TRMT=0" in sta.decoded and "UTXBF=0" in sta.decoded and sta.status == "bad"
    assert rep.verdict.startswith("BUG FOUND") and "URXDA=0" in rep.root_cause
    assert any("while (!U1STAbits.URXDA)" in f.c_line for f in rep.analysis.fixes)
    rpor = next(r for r in rep.analysis.rows if r.register == "RPOR2")
    assert "RP4R=3 (U1TX)" in rpor.decoded
    assert any("U1TX is on RP4" in u and "RP3" in u for u in rep.analysis.uncertain)
    assert any("Fcy 4 MHz stated" in n for n in rep.notes)


def test_uart_unknown_clock_is_uncertain_not_false_bug():
    e = ex("01_mplabx_uart_rx_empty.json")
    rep = vision_debug([], "UART1 9600 baud", fosc_hz=32e6, extraction=e)   # profile 16 MHz Fcy, not stated
    brg = next(r for r in rep.analysis.rows if r.register == "U1BRG")
    assert brg.status == "warn" and "9,615 @ Fcy 4 MHz" in brg.decoded and "38,462 @ Fcy 16 MHz" in brg.decoded
    assert rep.verdict.startswith("UNCERTAIN") and "Fcy = 4 MHz" in rep.verdict
    # explicit wrong clock -> real mismatch
    assert vision_debug([], "UART1 9600 baud", fosc_hz=32e6, clock_explicit=True, extraction=e).verdict.startswith("BUG")


def test_code_reading_rxreg_without_poll_flagged():
    code = "U1TXREG = 'a'; if (U1RXREG == 'a') LATDbits.LATD9 = 1;"
    rep = vision_debug([], "UART1 9600 baud Fcy 4 MHz loopback receive", code=code, extraction=ex("01_mplabx_uart_rx_empty.json"))
    assert any("without polling U1STAbits.URXDA" in c for c in rep.analysis.causes)


def test_lata_unimplemented_bits_not_a_bug_with_edc():
    dev = chipdb.get_device("PIC24FJ256GB110", path=str(FX / "PIC24FJ256GB110.PIC"), cache_dir="/tmp/cl_cache")
    rep = vision_debug([], "Write LATA = 0xBBAA and read it back", extraction=ex("03_mplabx_lata_porta.json"), device=dev)
    assert rep.verdict.startswith("OK: Not a bug"), rep.verdict
    assert "0xC6FF" in rep.root_cause and "RA11" in rep.root_cause
    t2 = next(r for r in rep.analysis.rows if r.register == "T2CON")
    assert t2.status == "info" and "not related" in t2.note
    assert not any("Timer2" in c for c in rep.analysis.causes) and not rep.analysis.fixes
    assert "VERDICT: OK" in format_report(rep)


def test_lata_builtin_mask_and_unknown_device():
    rep = vision_debug([], "Write LATA = 0xBBAA and read it back", extraction=ex("03_mplabx_lata_porta.json"), device="PIC24FJ256GB110")
    assert rep.verdict.startswith("OK: Not a bug")
    unk = vision_debug([], "Write LATA = 0xBBAA and read it back", extraction=ex("03_mplabx_lata_porta.json"), device="PIC24FJ999XX")
    assert unk.verdict.startswith("UNCERTAIN") and "unimplemented pins" in unk.verdict


def test_lata_real_mismatch_is_bug():
    e = normalize({"registers": {"LATA": "0x0001"}})
    rep = vision_debug([], "Write LATA = 0x0003", extraction=e, device="PIC24FJ256GB110")
    assert rep.verdict.startswith("BUG")


def test_normalize_keeps_port_and_pps_regs():
    e = ex("01_mplabx_uart_rx_empty.json")
    assert e.other["U1RXREG"] == 0 and e.registers["RPOR2"] == 3 and e.to_dict()["registers"]["U1RXREG"] == "0x0000"
