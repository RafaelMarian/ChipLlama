"""Arduino-core HAL (C++ glue): the same hal_*.h API implemented on pinMode/Serial/Wire/SPI/analogRead/LEDC.
Used for ESP32 (always) and for AVR boards in sketch mode."""
from __future__ import annotations

import re

j = "\n".join


def _gpio_no(p, name):
    bp = p.by_port.get(name)
    return p._ard_num(bp) if bp else -1


def hal_arduino_cpp(p, mods) -> str:
    esp = p.family == "esp32"
    mega = p.b["mcu"] == "ATMEGA2560"
    incs = ['#include "hal_gpio.h"', '#include "hal_timer.h"'] + [f'#include "hal_{m}.h"' for m in mods]
    if esp:
        p.cfg.append("/** ESP32 (arduino-esp32 core): pins are GPIO numbers, peripherals routed through the GPIO matrix. */")
    else:
        p.cfg.append(f"/** {p.b['title']} in Arduino sketch mode: pins are Arduino pin numbers. */")
    out = [f"/* hal_arduino.cpp - ChipLlama HAL on the Arduino core for {p.b['title']} ({p.b['mcu']}). */",
           "#include <Arduino.h>"]
    if "i2c" in mods:
        out.append("#include <Wire.h>")
    if "spi" in mods:
        out.append("#include <SPI.h>")
    out += ['extern "C" {', *incs, '#include "hal_config.h"', "}", "",
            "static uint8_t s_out[256 / 8];   /* shadow of output levels for hal_gpio_toggle() */", "",
            'extern "C" void hal_gpio_config(const hal_gpio_t *pin, hal_gpio_mode_t mode, uint8_t af)', "{",
            "    (void)af;", "    if ((pin == NULL) || (pin->pin == HAL_GPIO_NONE)) { return; }", "    switch (mode)", "    {",
            "    case HAL_GPIO_OUT: pinMode(pin->pin, OUTPUT); digitalWrite(pin->pin, LOW); break;",
            "    case HAL_GPIO_IN_PULLUP: pinMode(pin->pin, INPUT_PULLUP); break;",
            "    case HAL_GPIO_IN: pinMode(pin->pin, INPUT); break;",
            "    default: break;   /* peripheral / analog pins are set up by Serial, Wire, SPI, analogRead */", "    }", "}", "",
            'extern "C" void hal_gpio_write(const hal_gpio_t *pin, bool level)', "{",
            "    if ((pin == NULL) || (pin->pin == HAL_GPIO_NONE)) { return; }",
            "    digitalWrite(pin->pin, level ? HIGH : LOW);",
            "    if (level) { s_out[pin->pin >> 3] |= (uint8_t)(1U << (pin->pin & 7U)); }",
            "    else       { s_out[pin->pin >> 3] &= (uint8_t)~(1U << (pin->pin & 7U)); }", "}", "",
            'extern "C" bool hal_gpio_read(const hal_gpio_t *pin)', "{",
            "    return (pin != NULL) && (pin->pin != HAL_GPIO_NONE) && (digitalRead(pin->pin) == HIGH);", "}", "",
            'extern "C" void hal_gpio_toggle(const hal_gpio_t *pin)', "{",
            "    if ((pin == NULL) || (pin->pin == HAL_GPIO_NONE)) { return; }",
            "    hal_gpio_write(pin, (s_out[pin->pin >> 3] & (1U << (pin->pin & 7U))) == 0U);", "}", "",
            'extern "C" void hal_gpio_set_output(const hal_gpio_t *pin, bool output)', "{",
            "    if ((pin == NULL) || (pin->pin == HAL_GPIO_NONE)) { return; }",
            "    pinMode(pin->pin, output ? OUTPUT : INPUT);", "}", "",
            'extern "C" void hal_timer_init(void) { }',
            'extern "C" uint32_t hal_millis(void) { return (uint32_t)millis(); }',
            'extern "C" void hal_delay_ms(uint16_t ms) { delay(ms); }',
            'extern "C" void hal_delay_us(uint16_t us) { delayMicroseconds(us); }', ""]
    if "uart" in mods:
        cases_i, cases_o = [], []
        for n, u in sorted(p.uarts.items()):
            n = int(n)
            baud = int(u.get("baud", 115200))
            obj = ("Serial" if n == 0 else f"Serial{n}") if (esp or mega) else "Serial"
            if esp and n == 0 and p.b.get("chip") == "esp32c3":
                obj = "Serial0"
            p.cfg.append(f"#define HAL_UART{n}_BAUD     ({baud}UL)")
            if esp and n != 0:
                rx, tx = _gpio_no(p, u.get("rx")), _gpio_no(p, u.get("tx"))
                p.cfg += [f"#define HAL_UART{n}_RX_PIN   ({rx})", f"#define HAL_UART{n}_TX_PIN   ({tx})"]
                begin = f"{obj}.begin(HAL_UART{n}_BAUD, SERIAL_8N1, HAL_UART{n}_RX_PIN, HAL_UART{n}_TX_PIN)"
            else:
                begin = f"{obj}.begin(HAL_UART{n}_BAUD)"
            cases_i.append(f"    case HAL_UART_{n}: {begin}; return HAL_OK;")
            cases_o.append(f"    case HAL_UART_{n}: return &{obj};")
        out += ["static HardwareSerial *uart_of(hal_uart_id_t id)", "{", "    switch (id)", "    {", *cases_o,
                "    default: return NULL;", "    }", "}", "",
                'extern "C" hal_status_t hal_uart_init(hal_uart_id_t id)', "{", "    switch (id)", "    {", *cases_i,
                "    default: return HAL_ERR_PARAM;", "    }", "}", "",
                'extern "C" hal_status_t hal_uart_write_byte(hal_uart_id_t id, uint8_t byte)', "{",
                "    HardwareSerial *s = uart_of(id);", "    if (s == NULL) { return HAL_ERR_PARAM; }",
                "    (void)s->write(byte);", "    return HAL_OK;", "}", "",
                'extern "C" hal_status_t hal_uart_read_byte(hal_uart_id_t id, uint8_t *out)', "{",
                "    HardwareSerial *s = uart_of(id);", "    if ((s == NULL) || (out == NULL)) { return HAL_ERR_PARAM; }",
                "    if (s->available() <= 0) { return HAL_ERR_BUSY; }", "    *out = (uint8_t)s->read();", "    return HAL_OK;", "}", "",
                'extern "C" hal_status_t hal_uart_write_str(hal_uart_id_t id, const char *str)', "{",
                "    HardwareSerial *s = uart_of(id);", "    if ((s == NULL) || (str == NULL)) { return HAL_ERR_PARAM; }",
                "    (void)s->print(str);", "    return HAL_OK;", "}", "",
                'extern "C" hal_status_t hal_uart_write_int(hal_uart_id_t id, int32_t value)', "{",
                "    HardwareSerial *s = uart_of(id);", "    if (s == NULL) { return HAL_ERR_PARAM; }",
                "    (void)s->print((long)value);", "    return HAL_OK;", "}", ""]
    if "i2c" in mods:
        ids = sorted(int(i) for i in p.i2cs)
        objs = {i: ("Wire" if k == 0 else "Wire1") for k, i in enumerate(ids)}
        cases_o = [f"    case HAL_I2C_{i}: return &{o};" for i, o in objs.items()]
        cases_i = []
        for i, o in objs.items():
            if esp:
                pins = {}
                for b in p.binds:
                    for q in b["pins"].values():
                        if q.get("role") in ("scl", "sda") and re.search(rf"{i}$", q.get("fn") or ""):
                            pins[q["role"]] = _gpio_no(p, q.get("port_name"))
                p.cfg += [f"#define HAL_I2C{i}_SDA_PIN   ({pins.get('sda', -1)})", f"#define HAL_I2C{i}_SCL_PIN   ({pins.get('scl', -1)})"]
                cases_i.append(f"    case HAL_I2C_{i}: (void){o}.begin(HAL_I2C{i}_SDA_PIN, HAL_I2C{i}_SCL_PIN, 100000UL); return HAL_OK;")
            else:
                cases_i.append(f"    case HAL_I2C_{i}: {o}.begin(); {o}.setClock(100000UL); return HAL_OK;")
        out += ["static TwoWire *i2c_of(hal_i2c_id_t id)", "{", "    switch (id)", "    {", *cases_o, "    default: return NULL;", "    }", "}", "",
                'extern "C" hal_status_t hal_i2c_init(hal_i2c_id_t id)', "{", "    switch (id)", "    {", *cases_i,
                "    default: return HAL_ERR_PARAM;", "    }", "}", "",
                'extern "C" hal_status_t hal_i2c_write_read(hal_i2c_id_t id, uint8_t addr, const uint8_t *wr, size_t wlen, uint8_t *rd, size_t rlen)',
                "{", "    TwoWire *w = i2c_of(id);", "    size_t i;", "    if (w == NULL) { return HAL_ERR_PARAM; }",
                "    if (wlen > 0U)", "    {", "        uint8_t e;", "        w->beginTransmission(addr);",
                "        (void)w->write(wr, wlen);", "        e = w->endTransmission(rlen == 0U);",
                "        if (e == 2U || e == 3U) { return HAL_ERR_NACK; }", "        if (e != 0U) { return HAL_ERR_TIMEOUT; }", "    }",
                "    if (rlen > 0U)", "    {", "        if (w->requestFrom(addr, (uint8_t)rlen) != (uint8_t)rlen) { return HAL_ERR_NACK; }",
                "        for (i = 0U; i < rlen; i++) { rd[i] = (uint8_t)w->read(); }", "    }", "    return HAL_OK;", "}", "",
                'extern "C" hal_status_t hal_i2c_write(hal_i2c_id_t id, uint8_t addr, const uint8_t *data, size_t len)',
                "{", "    return hal_i2c_write_read(id, addr, data, len, NULL, 0U);", "}", "",
                'extern "C" hal_status_t hal_i2c_read(hal_i2c_id_t id, uint8_t addr, uint8_t *data, size_t len)',
                "{", "    return hal_i2c_write_read(id, addr, NULL, 0U, data, len);", "}", ""]
    if "spi" in mods:
        bus = next(iter(p.spis.values()), {})
        mode = int(bus.get("mode", 0))
        hz = int(bus.get("hz", 1e6))
        p.cfg += [f"#define HAL_SPI_HZ          ({hz}UL)", f"#define HAL_SPI_MODE        (SPI_MODE{mode})"]
        if esp:
            sck, miso, mosi = (_gpio_no(p, bus.get(k + "_pin")) for k in ("sck", "sdi", "sdo"))
            begin = f"SPI.begin({sck}, {miso}, {mosi});"
        else:
            begin = "SPI.begin();"
        out += ['extern "C" hal_status_t hal_spi_init(hal_spi_id_t id)', "{", "    (void)id;", f"    {begin}",
                "    SPI.beginTransaction(SPISettings(HAL_SPI_HZ, MSBFIRST, HAL_SPI_MODE));", "    return HAL_OK;", "}", "",
                'extern "C" uint8_t hal_spi_transfer(hal_spi_id_t id, uint8_t tx)', "{", "    (void)id;",
                "    return SPI.transfer(tx);", "}", "",
                'extern "C" hal_status_t hal_spi_transfer_buf(hal_spi_id_t id, const uint8_t *tx, uint8_t *rx, size_t len)', "{",
                "    size_t i;", "    for (i = 0U; i < len; i++)", "    {",
                "        uint8_t r = hal_spi_transfer(id, (tx != NULL) ? tx[i] : 0xFFU);", "        if (rx != NULL) { rx[i] = r; }", "    }",
                "    return HAL_OK;", "}", ""]
    if "adc" in mods:
        if esp:
            body = ["    analogReadResolution(12);", "    return HAL_OK;"]
            rd = ["    if (out == NULL) { return HAL_ERR_PARAM; }", "    *out = (uint16_t)analogRead(channel);   /* channel = GPIO number */",
                  "    return HAL_OK;"]
        else:
            base = 54 if mega else 14
            body = ["    return HAL_OK;"]
            rd = [f"    if ((out == NULL) || (channel > {15 if mega else 7}U)) {{ return HAL_ERR_PARAM; }}",
                  f"    *out = (uint16_t)analogRead((uint8_t)({base}U + channel));", "    return HAL_OK;"]
        out += ['extern "C" hal_status_t hal_adc_init(void)', "{", *body, "}", "",
                'extern "C" hal_status_t hal_adc_read(uint8_t channel, uint16_t *out)', "{", *rd, "}", ""]
    if "pwm" in mods and esp:
        init, us, pm = [], [], []
        for pw in p.pwms:
            b = pw["bind"]
            hz = 50 if b["drv"] == "servo" else int(float(b["params"].get("hz", 2000 if b["drv"] == "tone" else 1000)))
            g = _gpio_no(p, pw["pin"])
            nm = pw["name"]
            p.cfg += [f"/** {nm}: LEDC on GPIO{g}, {hz} Hz, 14-bit duty. */", f"#define HAL_{nm}_PIN        ({g})",
                      f"#define HAL_{nm}_HZ         ({hz}UL)"]
            init.append(f"    if (!ledcAttach(HAL_{nm}_PIN, HAL_{nm}_HZ, 14)) {{ return HAL_ERR_DEVICE; }}")
            us.append(f"    case HAL_PWM_{nm}: (void)ledcWrite(HAL_{nm}_PIN, (uint32_t)(((uint64_t)high_us * HAL_{nm}_HZ * 16383ULL) / 1000000ULL)); return HAL_OK;")
            pm.append(f"    case HAL_PWM_{nm}: (void)ledcWrite(HAL_{nm}_PIN, (16383UL * permille) / 1000UL); return HAL_OK;")
        out += ['extern "C" hal_status_t hal_pwm_init(void)', "{", *init, "    return HAL_OK;", "}", "",
                'extern "C" hal_status_t hal_pwm_set_us(hal_pwm_id_t ch, uint16_t high_us)', "{", "    switch (ch)", "    {", *us,
                "    default: return HAL_ERR_PARAM;", "    }", "}", "",
                'extern "C" hal_status_t hal_pwm_set_permille(hal_pwm_id_t ch, uint16_t permille)', "{",
                "    if (permille > 1000U) { return HAL_ERR_PARAM; }", "    switch (ch)", "    {", *pm,
                "    default: return HAL_ERR_PARAM;", "    }", "}", ""]
    return j(out)
