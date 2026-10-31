import json
from pathlib import Path
import pytest
from chipllama.families import blocks_fam as bf, toolchain as TC

EX = Path(__file__).resolve().parents[1] / "examples" / "blocks" / "families"
FILES = sorted(EX.glob("*.json"))


def test_every_board_has_blocks_examples():
    boards = {json.loads(f.read_text())["device"] for f in FILES}
    assert len(boards) == 8 and len(FILES) == 24


@pytest.mark.parametrize("f", FILES, ids=lambda f: f.stem)
def test_blocks_example_generates(f):
    r = bf.generate(json.loads(f.read_text()))
    assert not r["error"] and r["verified"], r
    assert "board_init();" in r["c"] and "for (;;)" in r["c"]
    assert "LAT" not in r["c"] and "TRIS" not in r["c"]


def test_pic_register_block_rejected():
    ws = {"circuit": "arduino_uno_blink", "blocks": {"blocks": [{"type": "cl_forever", "inputs": {"DO": {"block": {"type": "cl_pin_toggle", "fields": {"PIN": "RA0"}}}}}]}}
    assert "component blocks" in bf.generate(ws)["error"]


def test_missing_part_rejected():
    ws = {"circuit": "arduino_uno_blink", "blocks": {"blocks": [{"type": "cl_forever", "inputs": {"DO": {"block": {"type": "cl_servo_write", "fields": {"PART": "m1"}}}}}]}}
    assert "no servo" in bf.generate(ws)["error"]


@pytest.mark.skipif(not TC.find_avr_gcc(), reason="avr-gcc not installed")
def test_avr_blocks_compile():
    r = bf.generate(json.loads((EX / "arduino_uno_uart.json").read_text()), compile=True)
    assert r["compile"]["ok"] and r["verified"]


@pytest.mark.skipif(not TC.find_arm_gcc(), reason="arm-none-eabi-gcc not installed")
def test_stm32_blocks_compile():
    r = bf.generate(json.loads((EX / "stm32_bluepill_button.json").read_text()), compile=True)
    assert r["compile"]["ok"] and r["verified"]


@pytest.mark.parametrize("ex,dev", [("arduino_uno_i2c", "ARDUINO-UNO"), ("stm32_bluepill_i2c", "STM32-BLUEPILL"),
                                    ("esp32_devkitc_servo", "ESP32-DEVKITC")])
def test_audit_family_project_clean_and_pic_regs_flagged(ex, dev):
    from chipllama import audit as au, projgen
    root = Path(__file__).resolve().parents[1] / "examples" / "circuit" / "families"
    r = projgen.build(json.loads((root / f"{ex}.json").read_text()))
    src = {k: v for k, v in r["files"].items() if k.endswith((".c", ".h"))}
    d = au.audit_files(dict(src), device=dev).to_dict()
    assert not [f for f in d["findings"] if f["severity"] == "error"], d["findings"]
    main = next(k for k in src if k.endswith("main.c"))
    src[main] = src[main].replace("board_init();", "board_init();\n    TRISB = 0;")
    d = au.audit_files(src, device=dev).to_dict()
    assert any(f["rule"] == "PIC_REG_ON_FAMILY" for f in d["findings"])
