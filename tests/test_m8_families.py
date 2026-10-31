"""M8: MCU family plugins (Arduino AVR, STM32, ESP32)."""
import json
from pathlib import Path

import pytest

from chipllama import families as FAM, projgen
from chipllama.families import toolchain as TC
from chipllama.families.fam_agent import check_code, run_family_agent
from chipllama.config import Settings

EX = Path(__file__).resolve().parent.parent / "examples" / "circuit" / "families"
ALL = sorted(EX.glob("*.json"))


def test_examples_exist_for_every_board():
    kinds = {"blink", "button", "uart", "adc", "servo", "i2c"}
    for b in FAM.device_list():
        have = {f.stem.rsplit("_", 1)[1] for f in ALL if json.loads(f.read_text())["device"] == b["id"]}
        assert kinds <= have, (b["id"], kinds - have)


@pytest.mark.parametrize("f", ALL, ids=lambda f: f.stem)
def test_family_example_generates_and_verifies(f):
    r = projgen.build(json.loads(f.read_text()))
    assert r["erc_ok"] and r["verified"], r["check"]
    assert r["family"] in ("avr", "stm32", "esp32")
    assert not any(x["severity"] == "error" for x in r["check"]["findings"])
    assert "README.md" in r["files"]


def test_avr_arduino_mode_layout():
    d = json.loads((EX / "arduino_uno_blink.json").read_text())
    r = projgen.build(d, mode="arduino")
    assert r["mode"] == "arduino" and "arduino_uno_blink/arduino_uno_blink.ino" in r["files"]


def test_pic_untouched():
    assert not FAM.is_family("PIC24FJ256GB110") and FAM.family_of("PIC18F45K22") is None


def test_limits_have_datasheet_refs():
    for b in FAM.device_list():
        lim = FAM.limits(b["id"])
        assert lim["abs"] > 0 and lim["ref_abs"]


@pytest.mark.parametrize("name", ["arduino_uno_uart", "stm32_bluepill_i2c", "nucleo_f411re_servo", "stm32f030f4p6_adc"])
def test_family_compile_on_box(name):
    d = json.loads((EX / f"{name}.json").read_text())
    r = projgen.build(d)
    if (r["family"] == "avr" and not TC.find_avr_gcc()) or (r["family"] == "stm32" and not TC.find_arm_gcc()):
        pytest.skip("toolchain not installed")
    c = TC.compile_result(r)
    assert c["ok"], c["log"][-1500:]
    assert c["hex"].startswith(b":")


def test_doctor_rows_have_install_hints():
    for row in TC.status():
        assert row["ok"] or row["fix"]


def test_checker_catches_family_mistakes():
    uno, bp, esp = FAM.board("ARDUINO-UNO"), FAM.board("STM32-BLUEPILL"), FAM.board("ESP32-DEVKITC")
    assert not check_code("int main(void){ TCCR9A = 1; for(;;){} }", uno, "baremetal").ok
    assert check_code("#include <avr/io.h>\nint main(void){ DDRB |= (1<<DDB5); for(;;){} }", uno, "baremetal").ok
    assert not check_code("void f(void){ GPIOC->MODER = 1; }", bp, "baremetal").ok
    assert not check_code("void setup(){ledcSetup(0,50,16);} void loop(){}", esp, "arduino").ok
    assert not check_code("void setup(){pinMode(35, OUTPUT);} void loop(){}", esp, "arduino").ok


class _Seq:
    name = "fake"

    def __init__(self, *texts):
        self.texts, self.calls = list(texts), 0

    def complete(self, msgs):
        self.calls += 1
        return self.texts.pop(0)


def test_family_agent_self_corrects_avr():
    if not TC.find_avr_gcc():
        pytest.skip("avr-gcc missing")
    bad = "```c\n#include <avr/io.h>\nint main(void){ TCCR9A = 1; for(;;){} }\n```"
    good = ("```c\n#define F_CPU 16000000UL\n#include <avr/io.h>\n#include <util/delay.h>\n"
            "int main(void){ DDRB |= (1<<DDB5); for(;;){ PORTB ^= (1<<PORTB5); _delay_ms(500);} }\n```")
    s = Settings(device="ARDUINO-UNO")
    m = _Seq(bad, good)
    r = run_family_agent("blink D13", m, s, max_iters=3)
    assert r.status == "verified" and m.calls == 2


def test_family_agent_stm32_links_single_file():
    if not TC.find_arm_gcc():
        pytest.skip("arm-gcc missing")
    code = ("```c\n#include \"stm32f103xb.h\"\nint main(void){ RCC->APB2ENR |= RCC_APB2ENR_IOPCEN;"
            " GPIOC->CRH = (GPIOC->CRH & ~(0xFU<<20)) | (0x2U<<20); for(;;){ GPIOC->ODR ^= (1U<<13);"
            " for(volatile uint32_t i=0;i<100000;i++){} } }\n```")
    r = run_family_agent("blink PC13", _Seq(code), Settings(device="STM32-BLUEPILL"), max_iters=1)
    assert r.status == "verified", r.attempts[-1].compile.output[-800:]
