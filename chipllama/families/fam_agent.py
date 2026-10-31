"""Generate agent loop for the MCU families (M8): model -> family checker -> box/PC toolchain -> feedback."""
from __future__ import annotations

import re
import time
from types import SimpleNamespace

from ..checker import CheckReport
from ..compiler import CompileResult, parse_diagnostics
from ..models.base import Message, ModelError
from . import board as fam_board, stm32_data
from . import prompts as FP
from .checkers import avr_names, cmsis_names

_FENCE = re.compile(r"```(?:c|C|cpp|c\+\+|ino|arduino)?[ \t]*\n(.*?)```", re.S)
WEAK_SYS = ("/* ChipLlama: weak CMSIS hooks so a single-file program links */\n#include <stdint.h>\n"
            "__attribute__((weak)) uint32_t SystemCoreClock = {hz}UL;\n"
            "__attribute__((weak)) void SystemInit(void) {{ }}\n")
GPIO_STYLE = {"f1": "CRL/CRH (4 bits per pin: MODE + CNF; alternate function = CNF 10, AFIO for remap)",
              "f4": "MODER/OTYPER/PUPDR/AFR[0..1]", "f0": "MODER/OTYPER/PUPDR/AFR[0..1]"}


def extract(text: str) -> str | None:
    m = _FENCE.findall(text or "")
    return max(m, key=len) if m else None


def check_code(code: str, b: dict, mode: str) -> CheckReport:
    rep = CheckReport()
    fam = b["family"]
    if fam == "avr" and mode == "baremetal":
        names = avr_names(b["mcu"])
        used = set(re.findall(r"\b([A-Z][A-Z0-9]{1,9})\b", code))
        pat = re.compile(r"(TCCR|OCR|ICR|TIMSK|TIFR|UCSR|UBRR|UDR|TWBR|TWCR|TWSR|TWDR|SPCR|SPSR|SPDR|ADMUX|ADCSR|DDR|PORT|PIN|"
                         r"WGM|COM\d|OCIE|U2X|UCSZ|RXEN|TXEN|UDRE|RXC|TWINT|TWSTA|TWSTO|TWEN|SPE|MSTR|SPR|REFS|ADEN|ADSC|ADPS|MUX)\w*")
        cands = {u for u in used if pat.fullmatch(u)} - {"PORT", "PIN", "DDR"}
        for c in sorted(cands):
            if c not in names:
                rep.add("error", "AVR_UNKNOWN_REG", f"{c} is not a register/bit of {b['mcu']} (ATDF)")
        rep.add("info", "AVR_REGS", f"{len(cands)} register/bit names checked against the {b['mcu']} ATDF")
        if "main" not in code:
            rep.add("error", "NO_MAIN", "no int main(void)")
    elif fam == "stm32":
        sd = stm32_data(b["mcu"])
        series = sd["series"]
        names = cmsis_names(sd["header"]) | cmsis_names({"f1": "core_cm3.h", "f4": "core_cm4.h", "f0": "core_cm0.h"}[series])
        used = set(re.findall(r"\b((?:RCC|USART|I2C|SPI|ADC|TIM|GPIO|AFIO)_[A-Z0-9_]+)\b", code))
        for u in sorted(used - names):
            rep.add("error", "STM_UNKNOWN_MACRO", f"{u} is not defined in {sd['header']} (CMSIS)")
        for i in sorted(set(re.findall(r"\b(USART\d|I2C\d|SPI\d|TIM\d+|ADC\d?|GPIO[A-H])->", code))):
            if i not in names:
                rep.add("error", "STM_NO_INSTANCE", f"{i} does not exist on {b['mcu']}")
        if series == "f1" and re.search(r"GPIO[A-G]->(MODER|AFR)", code):
            rep.add("error", "STM_F1_GPIO", "STM32F1 GPIO has no MODER/AFR - use CRL/CRH (RM0008 §9.2)")
        if series != "f1" and re.search(r"GPIO[A-H]->CR[LH]\b", code):
            rep.add("error", "STM_GPIO", f"{b['mcu']} GPIO uses MODER/AFR, not CRL/CRH")
        rep.add("info", "STM_MACROS", f"{len(used)} CMSIS macros checked against {sd['header']}")
    else:   # arduino sketches
        if not re.search(r"\bvoid\s+setup\s*\(", code) or not re.search(r"\bvoid\s+loop\s*\(", code):
            rep.add("error", "ARD_NO_SETUP", "an Arduino sketch needs void setup() and void loop()")
        if fam == "esp32":
            if re.search(r"\bledc(Setup|AttachPin)\s*\(", code):
                rep.add("error", "ESP_LEDC_API", "ledcSetup/ledcAttachPin were removed in arduino-esp32 3.x: use ledcAttach(pin, freq, bits)")
            bad = {6, 7, 8, 9, 10, 11} if b["mcu"] == "ESP32" else {11, 12, 13, 14, 15, 16, 17}
            for n in re.findall(r"pinMode\s*\(\s*(\d+)\s*,\s*OUTPUT", code):
                if int(n) in bad:
                    rep.add("error", "ESP_FLASH_PIN", f"GPIO{n} is wired to the SPI flash on {b['mcu']}")
                if b["mcu"] == "ESP32" and 34 <= int(n) <= 39:
                    rep.add("error", "ESP_INPUT_ONLY", f"GPIO{n} is input-only on ESP32 (datasheet §2.2)")
        rep.add("info", "ARD_SKETCH", "sketch structure and board pin rules checked")
    return rep


def files_for(code: str, b: dict, mode: str, name: str = "cl_agent") -> dict:
    if mode == "arduino":
        return {f"{name}/{name}.ino": code}
    files = {"app/main.c": code}
    if b["family"] == "stm32":
        from .buildfiles import stm_files
        sd = stm32_data(b["mcu"])
        files.update(stm_files(SimpleNamespace(b=b, sd=sd, series=sd["series"])))
        files["app/cl_system.c"] = WEAK_SYS.format(hz=int(b["fcpu"]))
    return files


def compile_code(code: str, b: dict, mode: str) -> tuple[CompileResult, dict]:
    from .toolchain import compile_files
    name = "cl_agent"
    r = compile_files(files_for(code, b, mode, name), b["family"], b["id"], mode, name, b["mcu"])
    st = "skipped" if r["ok"] is None else ("ok" if r["ok"] else "error")
    log = r.get("log", "")
    return CompileResult(st, parse_diagnostics(log) if st == "error" else [], log[-4000:], [r.get("tool", "")],
                         reason=log[:300] if st == "skipped" else ""), r


def run_family_agent(request, model, settings, *, max_iters=4, on_event=None, context="", context_kind="debug", mode=None):
    from ..agent import AgentResult, Attempt
    emit = on_event or (lambda *_: None)
    b = fam_board(settings.device)
    mode = FP.mode_for(b["family"], mode or getattr(settings, "family_mode", None))
    res = AgentResult("error", request, backend=getattr(model, "name", "?"))
    extra = {"header": "", "gpio_style": ""}
    if b["family"] == "stm32":
        sd = stm32_data(b["mcu"])
        extra = {"header": sd["header"], "gpio_style": GPIO_STYLE[sd["series"]]}
    res.notes.append(f"family {b['family']} · {b['title']} · mode {mode} (datasheet register names checked; timing not emulated)")
    emit("spec", {"spec": None, "reference": None, "uart": None, "peripherals": {},
                  "family": b["family"], "board": b["id"], "mode": mode, "demo_fault_injection": False})
    msgs = [Message("system", FP.system_prompt(b, mode, extra)), Message("user", FP.user_prompt(request, b, mode))]
    if context.strip():
        msgs[1].text += ("\n\nSTARTING POINT (deterministic, already verified) - keep behaviour and pins:\n"
                         if context_kind == "blocks" else "\n\nCONTEXT:\n") + context.strip()[:6000]
    for i in range(1, max_iters + 1):
        t0 = time.time()
        emit("model_call", {"n": i, "backend": res.backend})
        try:
            text = model.complete(msgs)
        except ModelError as e:
            res.error = str(e)
            return res
        code = extract(text)
        if code is None:
            res.notes.append(f"attempt {i}: model returned no code block")
            msgs += [Message("assistant", text), Message("user", "No code block found. Return the complete file in one fenced block.")]
            emit("attempt", {"n": i, "error": "no code"})
            continue
        chk = check_code(code, b, mode)
        cmp_, _ = compile_code(code, b, mode)
        att = Attempt(i, code, chk, cmp_, time.time() - t0, text)
        res.attempts.append(att)
        emit("attempt", att.to_dict())
        if chk.ok and cmp_.ok:
            res.status = "verified"
            return res
        msgs += [Message("assistant", text),
                 Message("user", FP.feedback_prompt(cmp_.output if cmp_.status == "error" else "", chk.feedback_text()))]
    res.status = "failed"
    return res
