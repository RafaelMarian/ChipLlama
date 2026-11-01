"""Project generator for the M8 families (same HAL API as the PIC projects, so every driver is reused unchanged).

Back ends
  avr     bare-metal C for avr-gcc (Makefile)  - or an Arduino sketch (mode="arduino")
  stm32   CMSIS register-level C + our own startup/linker/Makefile for arm-none-eabi-gcc
  esp32   Arduino sketch for the arduino-esp32 core (arduino-cli)
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import circuit as circuitmod
from ..projgen import Project, _cid, _num, tree, project_name
from ..projgen import backends as B, hal_api as H
from ..projgen.drivers import DRIVERS
from . import board as fam_board

CMSIS = Path(__file__).resolve().parent.parent / "data" / "families" / "stm32" / "cmsis"
j = "\n".join

STM = {  # series -> clocks / identifiers
    "f1": {"sys": 72_000_000, "apb1": 36_000_000, "apb2": 72_000_000, "cpu": "cortex-m3", "hdr": "stm32f1xx.h",
           "desc": "HSE 8 MHz x9 PLL = 72 MHz, APB1 36 MHz, APB2 72 MHz, 2 flash wait states"},
    "f4": {"sys": 84_000_000, "apb1": 42_000_000, "apb2": 84_000_000, "cpu": "cortex-m4", "hdr": "stm32f4xx.h",
           "desc": "HSI 16 MHz PLL (M16 N336 P4) = 84 MHz, APB1 42 MHz, APB2 84 MHz, 2 flash wait states"},
    "f0": {"sys": 48_000_000, "apb1": 48_000_000, "apb2": 48_000_000, "cpu": "cortex-m0", "hdr": "stm32f0xx.h",
           "desc": "HSI/2 x12 PLL = 48 MHz, APB 48 MHz, 1 flash wait state + prefetch"},
}
APB2_SET = {"USART1", "USART6", "SPI1", "SPI4", "SPI5", "ADC1", "TIM1", "TIM8", "TIM9", "TIM10", "TIM11", "TIM15", "TIM16", "TIM17"}
ADV_TIM = {"TIM1", "TIM8", "TIM15", "TIM16", "TIM17"}

GPIO_FAM_H = """
#include "hal.h"

/** @brief Pin modes for hal_gpio_config(). */
typedef enum
{{
    HAL_GPIO_OUT = 0,       /**< push-pull output */
    HAL_GPIO_IN,            /**< input, no pull */
    HAL_GPIO_IN_PULLUP,     /**< input with the internal pull-up */
    HAL_GPIO_ANALOG,        /**< analog input (digital buffer off where possible) */
    HAL_GPIO_AF,            /**< peripheral output (alternate function, push-pull) */
    HAL_GPIO_AF_OD,         /**< peripheral open-drain (I2C) */
    HAL_GPIO_IN_PERIPH      /**< peripheral input (UART RX, SPI MISO) */
}} hal_gpio_mode_t;

{struct}

/** @brief Configure a pin. @param af alternate-function number (STM32 F0/F4), ignored elsewhere */
void hal_gpio_config(const hal_gpio_t *pin, hal_gpio_mode_t mode, uint8_t af);
/** @brief Drive an output pin. @param pin pin descriptor @param level true = high */
void hal_gpio_write(const hal_gpio_t *pin, bool level);
/** @brief Read the pin level. @return true when the pin is high */
bool hal_gpio_read(const hal_gpio_t *pin);
/** @brief Invert an output pin. */
void hal_gpio_toggle(const hal_gpio_t *pin);
/** @brief Set direction. @param output true = output */
void hal_gpio_set_output(const hal_gpio_t *pin, bool output);
"""

AVR_GPIO_T = """/** @brief One AVR pin: DDRx / PORTx / PINx and the bit mask. */
typedef struct
{
    volatile uint8_t       *ddr;   /**< direction register (1 = output) */
    volatile uint8_t       *port;  /**< output latch / pull-up enable */
    volatile const uint8_t *pin;   /**< pin level */
    uint8_t                 mask;  /**< bit mask of the pin */
} hal_gpio_t;"""

STM_GPIO_T = """/** @brief One STM32 pin: GPIO port and bit. */
typedef struct
{
    GPIO_TypeDef *port;    /**< GPIOA, GPIOB, ... (NULL = not connected) */
    uint16_t      mask;    /**< 1 << bit */
    uint8_t       bit;     /**< pin number 0..15 */
} hal_gpio_t;"""

ARD_GPIO_T = """/** @brief One pin, by its Arduino pin / GPIO number (0xFF = not connected). */
typedef struct
{
    uint8_t pin;
} hal_gpio_t;
/** @brief Unconnected pin number. */
#define HAL_GPIO_NONE       (0xFFU)"""

AVR_GPIO_C = """
#include "hal_gpio.h"

void hal_gpio_config(const hal_gpio_t *pin, hal_gpio_mode_t mode, uint8_t af)
{
    (void)af;
    if ((pin == NULL) || (pin->ddr == NULL)) { return; }
    switch (mode)
    {
    case HAL_GPIO_OUT:
    case HAL_GPIO_AF:
        *pin->port &= (uint8_t)~pin->mask;
        *pin->ddr |= pin->mask;
        break;
    case HAL_GPIO_IN_PULLUP:
        *pin->ddr &= (uint8_t)~pin->mask;
        *pin->port |= pin->mask;
        break;
    default:            /* inputs, analog, TWI (the peripheral overrides the port) */
        *pin->ddr &= (uint8_t)~pin->mask;
        *pin->port &= (uint8_t)~pin->mask;
        break;
    }
}

void hal_gpio_write(const hal_gpio_t *pin, bool level)
{
    if ((pin == NULL) || (pin->port == NULL)) { return; }
    if (level) { *pin->port |= pin->mask; }
    else       { *pin->port &= (uint8_t)~pin->mask; }
}

bool hal_gpio_read(const hal_gpio_t *pin)
{
    return (pin != NULL) && (pin->pin != NULL) && ((*pin->pin & pin->mask) != 0U);
}

void hal_gpio_toggle(const hal_gpio_t *pin)
{
    if ((pin == NULL) || (pin->port == NULL)) { return; }
    *pin->port ^= pin->mask;
}

void hal_gpio_set_output(const hal_gpio_t *pin, bool output)
{
    if ((pin == NULL) || (pin->ddr == NULL)) { return; }
    if (output) { *pin->ddr |= pin->mask; }
    else        { *pin->ddr &= (uint8_t)~pin->mask; }
}
"""


def stm_gpio_c(series: str) -> str:
    if series == "f1":
        en = "RCC->APB2ENR |= (1UL << (2U + idx)) | RCC_APB2ENR_AFIOEN;   /* IOPxEN + AFIO */"
        cfg = """    /* F1: 4-bit CNF/MODE nibble per pin in CRL (0-7) / CRH (8-15), RM0008 9.2.1 */
    switch (mode)
    {
    case HAL_GPIO_OUT:       nib = 0x2UL; break;   /* output push-pull, 2 MHz */
    case HAL_GPIO_AF:        nib = 0xBUL; break;   /* AF push-pull, 50 MHz */
    case HAL_GPIO_AF_OD:     nib = 0xFUL; break;   /* AF open-drain, 50 MHz */
    case HAL_GPIO_ANALOG:    nib = 0x0UL; break;
    case HAL_GPIO_IN_PULLUP: nib = 0x8UL; pin->port->BSRR = pin->mask; break;   /* pull-up: ODR = 1 */
    default:                 nib = 0x4UL; break;   /* floating input */
    }
    reg = (pin->bit < 8U) ? &pin->port->CRL : &pin->port->CRH;
    sh = (uint32_t)(pin->bit & 7U) * 4U;
    *reg = (*reg & ~(0xFUL << sh)) | (nib << sh);"""
        dirc = """    volatile uint32_t *reg;
    uint32_t sh;
    if (pin == NULL || pin->port == NULL) { return; }
    reg = (pin->bit < 8U) ? &pin->port->CRL : &pin->port->CRH;
    sh = (uint32_t)(pin->bit & 7U) * 4U;
    *reg = (*reg & ~(0xFUL << sh)) | ((output ? 0x2UL : 0x4UL) << sh);"""
        decl = "    volatile uint32_t *reg;\n    uint32_t sh;\n    uint32_t nib;"
    else:
        en = ("RCC->AHB1ENR |= (1UL << idx);   /* GPIOxEN */" if series == "f4" else
              "RCC->AHBENR |= (1UL << (17U + idx));   /* IOPxEN */")
        cfg = """    /* MODER 00 in / 01 out / 10 AF / 11 analog, OTYPER, PUPDR 01 = pull-up, AFR[0..1] 4 bits per pin */
    sh = (uint32_t)pin->bit * 2U;
    switch (mode)
    {
    case HAL_GPIO_OUT:       moder = 1UL; break;
    case HAL_GPIO_AF:
    case HAL_GPIO_AF_OD:
    case HAL_GPIO_IN_PERIPH: moder = 2UL; break;
    case HAL_GPIO_ANALOG:    moder = 3UL; break;
    default:                 moder = 0UL; break;
    }
    if (mode == HAL_GPIO_AF_OD) { pin->port->OTYPER |= pin->mask; }
    else                        { pin->port->OTYPER &= ~(uint32_t)pin->mask; }
    pull = ((mode == HAL_GPIO_IN_PULLUP) || (mode == HAL_GPIO_AF_OD) || (mode == HAL_GPIO_IN_PERIPH)) ? 1UL : 0UL;
    pin->port->PUPDR = (pin->port->PUPDR & ~(3UL << sh)) | (pull << sh);
    pin->port->OSPEEDR = (pin->port->OSPEEDR & ~(3UL << sh)) | (2UL << sh);
    if (moder == 2UL)
    {
        uint32_t a = (uint32_t)(pin->bit & 7U) * 4U;
        pin->port->AFR[pin->bit >> 3] = (pin->port->AFR[pin->bit >> 3] & ~(0xFUL << a)) | ((uint32_t)(af & 0xFU) << a);
    }
    pin->port->MODER = (pin->port->MODER & ~(3UL << sh)) | (moder << sh);"""
        dirc = """    uint32_t sh;
    if (pin == NULL || pin->port == NULL) { return; }
    sh = (uint32_t)pin->bit * 2U;
    pin->port->MODER = (pin->port->MODER & ~(3UL << sh)) | ((output ? 1UL : 0UL) << sh);"""
        decl = "    uint32_t sh;\n    uint32_t moder;\n    uint32_t pull;"
    return f"""
#include "hal_gpio.h"

void hal_gpio_config(const hal_gpio_t *pin, hal_gpio_mode_t mode, uint8_t af)
{{
{decl}
    uint32_t idx;
    if ((pin == NULL) || (pin->port == NULL)) {{ return; }}
    idx = ((uint32_t)pin->port - (uint32_t)GPIOA_BASE) / 0x400UL;
    {en}
    (void)af;
{cfg}
}}

void hal_gpio_write(const hal_gpio_t *pin, bool level)
{{
    if ((pin == NULL) || (pin->port == NULL)) {{ return; }}
    pin->port->BSRR = level ? (uint32_t)pin->mask : ((uint32_t)pin->mask << 16);
}}

bool hal_gpio_read(const hal_gpio_t *pin)
{{
    return (pin != NULL) && (pin->port != NULL) && ((pin->port->IDR & pin->mask) != 0U);
}}

void hal_gpio_toggle(const hal_gpio_t *pin)
{{
    if ((pin == NULL) || (pin->port == NULL)) {{ return; }}
    hal_gpio_write(pin, (pin->port->ODR & pin->mask) == 0U);
}}

void hal_gpio_set_output(const hal_gpio_t *pin, bool output)
{{
{dirc}
}}
"""


I2C_COMMON = """
hal_status_t hal_i2c_write(hal_i2c_id_t id, uint8_t addr, const uint8_t *data, size_t len)
{
    return hal_i2c_write_read(id, addr, data, len, NULL, 0U);
}

hal_status_t hal_i2c_read(hal_i2c_id_t id, uint8_t addr, uint8_t *data, size_t len)
{
    return hal_i2c_write_read(id, addr, NULL, 0U, data, len);
}
"""


class FamilyProject(Project):
    """Project for a family board; reuses Project.collect/drivers/amalgamate, replaces the PIC back end."""

    def __init__(self, d: dict, ics: str = "", packs: str = "", mode: str | None = None):
        self.d = d
        self.ics = ics
        self.res = circuitmod.check(d, packs)
        self.b = fam_board(d.get("device", ""))
        self.device = self.b["id"]
        self.family = self.b["family"]
        self.mode = mode or ("arduino" if self.family == "esp32" else "baremetal")
        if self.family == "esp32":
            self.mode = "arduino"
        if self.family == "stm32":
            self.mode = "baremetal"
        self.dev = None
        self.kind = self.family
        self.comp = {"avr": "avr-gcc", "stm32": "arm-none-eabi-gcc", "esp32": "arduino-cli"}[self.family]
        if self.mode == "arduino":
            self.comp = "arduino-cli"
        if self.family == "stm32":
            from . import stm32_data
            self.sd = stm32_data(self.b["mcu"])
            self.series = self.sd["series"]
            self.fosc = self.fcy = STM[self.series]["sys"]
            self.clock_desc = STM[self.series]["desc"]
        else:
            self.fosc = self.fcy = self.b["fcpu"]
            self.clock_desc = (f"{self.b['fcpu'] / 1e6:g} MHz crystal on the board" if self.family == "avr" else
                               f"{self.b['fcpu'] / 1e6:g} MHz (arduino-esp32 default)")
        self.cfg_over, self.clock_lines = {}, []
        self.notes: list[str] = []
        self.findings = [f for f in self.res["findings"] if f["severity"] == "error"]
        self.files: dict[str, str] = {}
        self.cfg: list[str] = []
        self.binds = [b for b in self.res["bindings"] if b.get("drv")]
        self.gpio: dict = {}
        self.uarts, self.i2cs, self.spis, self.pwms, self.adcs = {}, set(), {}, [], set()
        self.specs: dict = {}
        self.inst: list = []
        self.mods: set = set()
        self.by_port = {p["port"]: p for p in self.b["pins"] if p.get("port")}
        self.ard = self.mode == "arduino"

    # ------------------------------------------------------------------ helpers
    def _bp(self, name):
        return self.by_port.get(name)

    def _ard_num(self, p: dict) -> int:
        if self.family == "esp32":
            return int(p["gpio"])
        lab = p["label"]
        if lab.startswith("D"):
            return int(lab[1:])
        base = 54 if self.b["mcu"] == "ATMEGA2560" else 14
        return base + int(lab[1:])

    def _gp(self, p):
        name = p.get("port_name") if isinstance(p, dict) else p
        bp = self._bp(name)
        if not bp:
            return "BOARD_PIN_NONE"
        if self.ard:
            return f"BOARD_PIN({self._ard_num(bp)})"
        m = re.match(r"P([A-Z])(\d+)$", name)
        return f"BOARD_PIN({m.group(1)}, {m.group(2)})"

    def _instance(self, b):
        cid, drv, t, init, ram, extra = super()._instance(b)
        if drv == "uart_dev":
            fns = [p.get("fn", "") for p in b["pins"].values()]
            n = next((_num(f) for f in fns if re.match(r"U\d", f)), 1)
            init = f"{{ HAL_UART_{n} }}"
        return cid, drv, t, init, ram, extra

    def collect(self):
        super().collect()
        # PWM channel ids from the function names (unique)
        seen: dict = {}
        for pw in self.pwms:
            base = pw["fn"].replace("TIM", "TIM").upper()
            seen[base] = seen.get(base, 0) + 1
            pw["name"] = base if seen[base] == 1 else f"{base}_{seen[base]}"
            pw["id"] = f"HAL_PWM_{pw['name']}"

    # ------------------------------------------------------------------ pins for board_init
    def pin_modes(self):
        """[(port_name, mode, af, why)]"""
        out, seen = [], set()
        for b in self.binds:
            if b["drv"] not in DRIVERS:
                continue
            for k, p in b["pins"].items():
                name, role, fn = p.get("port_name"), p.get("role"), p.get("fn") or ""
                if not name or name in seen:
                    continue
                seen.add(name)
                bp = self._bp(name) or {}
                af = (bp.get("af") or {}).get(fn)
                af = af if isinstance(af, int) else 0
                if role == "an":
                    mode = "ANALOG"
                elif role in ("scl", "sda"):
                    mode = "AF_OD"
                elif re.match(r"U\d?TX", fn) or fn.startswith(("SCK", "SDO")) or role == "pwm":
                    mode = "AF"
                elif re.match(r"U\d?RX", fn) or fn.startswith("SDI"):
                    mode = "IN_PERIPH"
                elif role in ("out", "cs"):
                    mode = "OUT"
                else:
                    g = self.gpio.get(name, {})
                    mode = "IN_PULLUP" if g.get("pullup") else "IN"
                if self.family == "avr" and mode in ("AF", "IN_PERIPH", "AF_OD") and role != "pwm":
                    continue          # USART / TWI / SPI take the pins over themselves (SPI DDR set in hal_spi_init)
                if self.ard and self.family == "esp32" and mode in ("AF", "AF_OD", "IN_PERIPH", "ANALOG"):
                    continue          # GPIO matrix routed by the Arduino core (Serial.begin(pins), Wire.begin(pins), ledcAttach)
                out.append((name, mode, af, f"{b['id']}.{k} {fn}"))
        return out

    # ------------------------------------------------------------------ HAL
    def hal(self):
        dn = self.device
        hdr = lambda n, br, body: H.header(n, br, body, dn)  # noqa: E731
        if self.family == "avr" and not self.ard:
            pre = f"#ifndef F_CPU\n#define F_CPU               ({int(self.fcy)}UL)\n#endif\n#include <avr/io.h>\n"
            reg = "uint8_t"
        elif self.family == "stm32":
            pre = f'#include "{STM[self.series]["hdr"]}"\n'
            reg = "uint32_t"
        else:
            pre, reg = "", "uint32_t"
        hal_h = H.HAL_H.format(reg_t=reg, fcy=int(self.fcy))
        hal_h = hal_h.replace("#include <stddef.h>\n", "#include <stddef.h>\n" + pre, 1)
        if self.family == "stm32":
            hal_h += "\n/** @brief Clock tree setup (PLL, flash wait states, bus prescalers) - called by board_init(). */\nvoid hal_clock_init(void);\n"
        self.files["hal/hal.h"] = hdr("hal.h", "Common HAL types and status codes.", hal_h)
        struct = AVR_GPIO_T if (self.family == "avr" and not self.ard) else STM_GPIO_T if self.family == "stm32" else ARD_GPIO_T
        self.files["hal/hal_gpio.h"] = hdr("hal_gpio.h", "Digital I/O on a pin descriptor.", GPIO_FAM_H.format(struct=struct))
        self.files["hal/hal_timer.h"] = hdr("hal_timer.h", "Millisecond tick and busy-wait delays.", H.TIMER_H)
        mods = [m for m in ("uart", "i2c", "spi", "adc", "pwm") if m in self.mods or (m == "uart" and self.uarts)]
        ids = {"uart": [str(i) for i in sorted(self.uarts)], "i2c": [str(i) for i in sorted(self.i2cs)],
               "spi": [str(i) for i in sorted(self.spis)], "pwm": [pw["name"] for pw in self.pwms], "adc": []}
        for m in mods:
            hname = getattr(H, f"{m.upper()}_H")
            body = hname.format(ids=self._ids(m, ids[m])) if "{ids}" in hname else hname
            if m == "adc":
                mx, mv = (1023, 5000) if self.family == "avr" else (4095, 3300)
                body = body.replace("(1023U)", f"({mx}U)").replace("(3300U)", f"({mv}U)").replace("10-bit", f"{mx.bit_length()}-bit")
                body = body.replace("@param channel ANx number", "@param channel " + (
                    "GPIO number (ADC pin)" if self.family == "esp32" else "ADC channel (ANx / ADC_INx)"))
            self.files[f"hal/hal_{m}.h"] = hdr(f"hal_{m}.h", f"{m.upper()} driver API.", body)
        self.hal_mods = mods
        if self.ard:
            from .arduino import hal_arduino_cpp
            self.files["hal/hal_arduino.cpp"] = hal_arduino_cpp(self, mods)
            if self.family == "avr" and "pwm" in mods:
                from .avr import pwm_c
                self.files["hal/hal_pwm.c"] = H.src("hal_pwm.c", "PWM on the ATmega 16-bit / Timer2 output compares.",
                                                    pwm_c(self), dn)
            return
        if self.family == "avr":
            from . import avr as A
            self.files["hal/hal_gpio.c"] = H.src("hal_gpio.c", "Digital I/O.", AVR_GPIO_C, dn)
            self.files["hal/hal_timer.c"] = H.src("hal_timer.c", "1 ms tick on Timer0 (CTC).", A.timer_c(self), dn)
            gen = {"uart": A.uart_c, "i2c": A.i2c_c, "spi": A.spi_c, "adc": A.adc_c, "pwm": A.pwm_c}
        else:
            from . import stm32 as S
            self.files["hal/hal_gpio.c"] = H.src("hal_gpio.c", "Digital I/O.", stm_gpio_c(self.series), dn)
            self.files["hal/hal_timer.c"] = H.src("hal_timer.c", "1 ms SysTick and delays.", S.timer_c(self), dn)
            self.files["hal/hal_clock.c"] = H.src("hal_clock.c", "Clock tree: " + self.clock_desc, S.clock_c(self), dn)
            gen = {"uart": S.uart_c, "i2c": S.i2c_c, "spi": S.spi_c, "adc": S.adc_c, "pwm": S.pwm_c}
        for m in mods:
            self.files[f"hal/hal_{m}.c"] = H.src(f"hal_{m}.c", f"{m.upper()} for {self.b['mcu']}.", gen[m](self), dn)

    # ------------------------------------------------------------------ board
    def board(self):
        dn = self.device
        incs = sorted({f'#include "{d}.h"' for d in self.used_drivers()})
        insts = [self._instance(b) for b in self.binds if b["drv"] in DRIVERS]
        self.inst = insts
        if self.ard:
            pdef = ["/** @brief Pin descriptor from an Arduino pin / GPIO number. */",
                    "#define BOARD_PIN(n)          { (uint8_t)(n) }", "/** @brief Unconnected pin. */",
                    "#define BOARD_PIN_NONE        { HAL_GPIO_NONE }"]
        elif self.family == "avr":
            pdef = ["/** @brief Pin descriptor for port letter + bit (DDR / PORT / PIN registers). */",
                    "#define BOARD_PIN(port, bit)  { &DDR##port, &PORT##port, &PIN##port, (uint8_t)(1U << (bit)) }",
                    "/** @brief Unconnected pin. */", "#define BOARD_PIN_NONE        { NULL, NULL, NULL, 0U }"]
        else:
            pdef = ["/** @brief Pin descriptor for port letter + bit. */",
                    "#define BOARD_PIN(port, bit)  { GPIO##port, (uint16_t)(1U << (bit)), (uint8_t)(bit) }",
                    "/** @brief Unconnected pin. */", "#define BOARD_PIN_NONE        { NULL, 0U, 0U }"]
        h = [*incs, '#include "hal_gpio.h"', "", *pdef, ""]
        for cid, drv, t, init, ram, extra in insts:
            b = next(x for x in self.binds if _cid(x["id"]) == cid)
            pins = ", ".join(f"{k}={self._label(p.get('port_name'))}" for k, p in b["pins"].items())
            h.append(f"/** @brief {b['id']} ({b['type']}): {pins or 'bus device'}. */")
            h.append(f"extern const {t} {cid};")
        h += ["", "/** @brief Clock, pin directions, pull-ups and peripheral pin functions - call first in main(). */",
              "void board_init(void);"]
        self.files["board/board.h"] = H.header("board.h", f"{self.b['title']}: pin map and component instances.", "\n".join(h), dn)
        c = ['#include "board.h"', '#include "hal_config.h"', ""]
        for cid, drv, t, init, ram, extra in insts:
            c += ram + extra + [f"const {t} {cid} = {init};", ""]
        modes = self.pin_modes()
        for i, (name, mode, af, why) in enumerate(modes):
            c.append(f"static const hal_gpio_t s_pin{i} = {self._gp({'port_name': name})};   /* {self._label(name)}: {why} */")
        c += ["", "void board_init(void)", "{"]
        if self.family == "stm32":
            c.append("    hal_clock_init();")
        for i, (name, mode, af, why) in enumerate(modes):
            c.append(f"    hal_gpio_config(&s_pin{i}, HAL_GPIO_{mode}, {af}U);")
        if len(c) and c[-1] == "{":
            c.append("    /* nothing to configure */")
        c.append("}")
        self.files["board/board.c"] = H.src("board.c", "Board pin map, component instances and board_init().", "\n".join(c), dn)

    def _label(self, name):
        bp = self._bp(name)
        return f"{bp['label']}/{name}" if bp and bp["label"] != name else str(name)

    def config(self):
        body = ['#include "hal.h"', "", f"/* Clock: {self.clock_desc}. */", f"#define HAL_FOSC_HZ         ({int(self.fosc)}UL)",
                "", *self.cfg]
        self.files["hal/hal_config.h"] = H.header("hal_config.h", "Every timing constant (computed by the ChipLlama solvers).",
                                                  "\n".join(body), self.device)

    def main(self):
        super().main()
        if self.ard:
            self.files["app/main.c"] = self.files["app/main.c"].replace("int main(void)", "int cl_app_main(void)")

    def build_files(self):
        from .buildfiles import family_build_files
        family_build_files(self)

    def verify(self):
        from .checkers import check_project
        return check_project(self)

    def run(self) -> dict:
        self.name = project_name(self.d)
        res = super().run()
        if self.ard:
            self.files = arduino_layout(self.files, self.name)
            res["files"] = self.files
            res["tree"] = tree(self.files)
        res.update({"family": self.family, "board": self.device, "mcu": self.b["mcu"], "mode": self.mode,
                    "compiler": self.comp, "backend": f"{self.family}-{self.mode}"})
        return res


def arduino_layout(files: dict, name: str) -> dict:
    """Flatten into an Arduino sketch folder: <name>/<name>.ino + <name>/src/* (one include dir)."""
    out = {}
    for p, t in files.items():
        if p.endswith((".c", ".h", ".cpp")):
            out[f"{name}/src/{Path(p).name}"] = t
        else:
            out[p] = t
    out[f"{name}/{name}.ino"] = (
        f"/* {name}.ino - generated by ChipLlama. The application is plain C in src/ (HAL + drivers + board + app);\n"
        " * this sketch only hands control to it. Build: arduino-cli compile --fqbn <board> . */\n"
        'extern "C" int cl_app_main(void);\n\n'
        "void setup()\n{\n    (void)cl_app_main();   /* never returns: super-loop inside */\n}\n\nvoid loop()\n{\n}\n")
    return out


def build(d: dict, ics: str = "", packs: str = "", mode: str | None = None) -> dict:
    return FamilyProject(d, ics, packs, mode).run()
