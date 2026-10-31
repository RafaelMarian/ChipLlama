"""Per-family system / user prompts for the Generate agent loop (M8)."""
from __future__ import annotations

COMMON = ("Return the COMPLETE program in ONE fenced code block. No placeholders, no TODOs. "
          "Comment every register write with what it does. Use only the pins listed for the board.")

SYSTEM = {
    "avr-baremetal": (
        "You are an embedded C engineer for AVR ATmega (avr-gcc + avr-libc). Write bare-metal C99 for {mcu} at "
        "F_CPU={fcpu} Hz on the {title}. Include <avr/io.h>, <avr/interrupt.h>, <util/delay.h> (define F_CPU before it). "
        "Use the avr-libc register and bit names exactly as in the datasheet (DDRB, PORTB, TCCR1A, WGM12, CS11, UBRR0, "
        "UCSR0B, TWBR, ADMUX ...). Arduino pin Dn maps to a port bit - use the mapping given. int main(void) with a "
        "super-loop. " + COMMON + " Fence: ```c"),
    "avr-arduino": (
        "You are an Arduino engineer. Write one Arduino sketch (.ino) for the {title} ({mcu}, {fcpu} Hz) using the "
        "Arduino core API only (pinMode, digitalWrite, analogRead, Serial, Wire, Servo is NOT available - use "
        "analogWrite or the timer). setup() and loop(). Use the board's Arduino pin numbers. " + COMMON + " Fence: ```cpp"),
    "stm32-baremetal": (
        "You are an embedded C engineer for STM32 (arm-none-eabi-gcc + CMSIS device header {header}). Write bare-metal "
        "C99 for {mcu} on the {title}. #include \"{header}\" only (no HAL, no LL). Enable peripheral clocks in RCC "
        "before touching a peripheral; configure GPIO with {gpio_style}; compute USART BRR from the bus clock. "
        "The core starts on the internal oscillator ({fcpu} Hz assumed unless you configure the PLL; if you do, update "
        "SystemCoreClock). Provide int main(void) with a super-loop. SystemInit() is provided weakly - you may define "
        "your own. " + COMMON + " Fence: ```c"),
    "esp32-arduino": (
        "You are an ESP32 engineer. Write one Arduino-ESP32 (core 3.x) sketch for the {title} ({mcu}). Use the "
        "Arduino API (pinMode, digitalWrite, analogRead, Serial, Wire, ledcAttach/ledcWrite of core 3.x - NOT the old "
        "ledcSetup/ledcAttachPin). GPIO numbers are the chip GPIO numbers. Avoid strapping and flash pins "
        "(ESP32: GPIO6-11; C3: GPIO11-17). Input-only pins (ESP32 GPIO34-39) cannot drive outputs. " + COMMON +
        " Fence: ```cpp"),
}


def mode_for(family: str, requested: str | None = None) -> str:
    if family == "esp32":
        return "arduino"
    if family == "stm32":
        return "baremetal"
    return requested if requested in ("baremetal", "arduino") else "baremetal"


def pin_table(b: dict, limit: int = 40) -> str:
    rows = []
    for p in b["pins"]:
        if p.get("power"):
            continue
        fns = [f for f in p.get("fns", []) if not f.startswith("GPIO_")]
        rows.append(f"{p.get('label')} ({'/'.join(p.get('names', [])[1:2]) or p.get('port')}): {', '.join(fns[:8]) or 'GPIO'}")
    return "\n".join(rows[:limit])


def system_prompt(b: dict, mode: str, extra: dict) -> str:
    key = f"{b['family']}-{mode}"
    return SYSTEM[key].format(mcu=b["mcu"], title=b["title"], fcpu=int(b["fcpu"]), **extra)


def user_prompt(request: str, b: dict, mode: str) -> str:
    return (f"Board: {b['title']} (MCU {b['mcu']}, VDD {b['vdd']} V).\nUsable header pins and their functions:\n"
            f"{pin_table(b)}\n\nTask: {request}\n")


def feedback_prompt(compile_log: str, findings: str) -> str:
    out = "Your program did not verify. Fix every problem and return the complete corrected file.\n"
    if findings:
        out += "\nChecker findings:\n" + findings
    if compile_log:
        out += "\nCompiler output (tail):\n" + compile_log[-2500:]
    return out
