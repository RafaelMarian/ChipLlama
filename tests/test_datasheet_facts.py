"""Facts verified against DS39897C (PIC24FJ256GB110 family), page numbers in comments."""
import subprocess
import sys
from pathlib import Path

from chipllama import chipdb, i2c, pins, power
from tests.test_batch2 import GOOD_I2C, REQ_I2C, codes

R = chipdb.REGISTERS


def test_i2c_pins_100pin():  # p.7 pin diagram, p.24 pin table
    assert i2c.PINS == {1: ("RA14", "RA15", 66, 67), 2: ("RA2", "RA3", 58, 59), 3: ("RE6", "RE7", 4, 5)}


def test_ics_pairs():  # p.7, p.20 (PGEC, PGED)
    assert pins.ICS == {"PGx1": ("RB1", "RB0"), "PGx2": ("RB6", "RB7"), "PGx3": ("RB5", "RB4")}


def test_ic_con2_reset_and_pages():  # Reg 13-1 p.171, 13-2 p.172, reset 0x000D (SYNCSEL = Timer3), map p.47
    assert R["IC1CON2"].reset == 0x000D
    assert R["IC1CON1"].page == 171 and R["IC1CON2"].page == 172


def test_port_and_odc_masks():  # Tables 4-12..4-18 p.51-52; RF8 = pin 53 (p.7, p.23)
    want = {"A": 0xC6FF, "B": 0xFFFF, "C": 0xF01E, "D": 0xFFFF, "E": 0x03FF, "F": 0x313F, "G": 0xF3CF}
    for p, m in want.items():
        assert R[f"ODC{p}"].implemented_mask == m, p
    assert R["PORTA"].implemented_mask == 0xC6FF


def test_power_from_dc_tables():  # Tables 29-4/5/6 p.314-316, 3.3 V 25 C
    e = power.estimate(16e6)
    assert (e["typ"]["run_ma"], e["max"]["run_ma"]) == (18.2, 24.0)
    assert (e["typ"]["idle_ma"], e["max"]["idle_ma"]) == (4.4, 5.6)
    assert power.estimate(4e6)["run_ma"] == 4.3 and power.estimate(1e6, mode="idle")["awake_ma"] == 0.3
    s = power.estimate(16e6, duty=0.0, wdt=True)
    assert abs(s["sleep_ma"] - 0.0068) < 1e-9 and s["max"]["sleep_ma"] == 0.015
    assert 4.3 < power.estimate(8e6)["run_ma"] < 18.2


def test_i2c_odc_findings():
    rep, _ = codes(GOOD_I2C.replace("static void init(void)\n{", "static void init(void)\n{\n    ODCA = 0xC000;\n    ODCF = 0x0040;"), "i2c", REQ_I2C)
    got = {f.code: f.severity for f in rep.findings}
    assert got.get("I2C_ODC") == "info" and got.get("ODC_UNIMPL") == "warning"


def test_compile_blocks_script_runs():
    root = Path(__file__).resolve().parent.parent
    p = subprocess.run([sys.executable, str(root / "scripts" / "compile_blocks_examples.py")], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "FAILED" not in p.stdout and ("VERIFIED" in p.stdout or "SKIPPED" in p.stdout)
