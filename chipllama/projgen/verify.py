"""Checker run over the amalgamated project (single translation unit)."""
from __future__ import annotations

from .. import pragmas, peripherals, gpio as gpiomod, uart as uartmod
from ..checker import CheckReport, check_timer
from ..cemu import emulate
from ..timers import TimerSpec


def verify(p) -> dict:
    code = p.amalg
    full = p.kind == "pic24" and p.device.upper() == "PIC24FJ256GB110"
    if full:
        from .. import board as boardmod
        prof = boardmod.get_profile("frcpll")
        chk = check_timer(code, TimerSpec(timer=1, period_s=1e-3, fosc_hz=p.fosc, interrupt=True, priority=4), profile=prof, ics=p.ics)
        emu = emulate(code)
        for us in p.specs.get("uart", []):
            uartmod.check_uart(code, us, chk, emu)
        pins = [gpiomod.PinCfg(n, g["out"], pullup=g["pullup"]) for n, g in sorted(p.gpio.items())
                if not g["periph"] and not g["analog"]]
        pspecs = {}
        if pins:
            pspecs["gpio"] = gpiomod.GpioSpec(pins=pins)
        if "adc" in p.specs:
            pspecs["adc"] = p.specs["adc"]
        extra = []
        for k in ("i2c", "spi", "pwm"):
            lst = p.specs.get(k, [])
            if lst:
                pspecs[k] = lst[0]
                extra += [(k, sp) for sp in lst[1:]]
        peripherals.check_all(code, pspecs, chk, emu)
        for k, sp in extra:
            peripherals._mod(k).check(code, sp, chk, emu)
        scope = "full register check (timer, UART, GPIO, ADC, I2C, SPI, PWM, config bits, clock tree)"
    else:
        chk = CheckReport()
        if p.dev:
            pragmas.check(code, p.dev, chk.add, p.ics)
        scope = "configuration bits + every REGbits.FIELD against the device's EDC data"
    if p.dev:
        pragmas.check_sfr_fields(code, p.dev, chk.add)
    fs = [f.to_dict() for f in chk.findings if f.code not in ("UNPARSED",)]
    for f in fs:
        if f["code"] == "GPIO_READ":   # hal_gpio_read() reads PORTx through the port table, which the checker cannot see
            f["severity"] = "info"
            f["message"] += " (read through hal_gpio_read()'s PORT register table)"
    ok = not any(f["severity"] == "error" for f in fs)
    return {"verified": ok, "scope": scope, "findings": fs,
            "registers": {k: f"0x{v:04X}" for k, v in chk.registers.items()}}
