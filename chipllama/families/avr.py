"""ATmega328P / ATmega2560 bare-metal HAL back end (avr-gcc + avr-libc register names, checked against the ATDF)."""
from __future__ import annotations

from ..projgen.backends import UART_COMMON, SPI_COMMON

j = "\n".join
SPI_PINS = {"ATMEGA328P": {"ss": 2, "sck": 5, "mosi": 3}, "ATMEGA2560": {"ss": 0, "sck": 1, "mosi": 2}}


def ubrr(f: float, baud: int) -> tuple[int, int, float]:
    """(UBRR, U2X, error %) - the setting with the lower baud error (ATmega328P §20.10 Table 20-7)."""
    best = None
    for u2x, div in ((0, 16), (1, 8)):
        n = max(0, min(4095, round(f / (div * baud)) - 1))
        err = (f / (div * (n + 1)) / baud - 1) * 100
        if best is None or abs(err) < abs(best[2]) - 1e-9:
            best = (n, u2x, err)
    return best


def timer_c(p) -> str:
    f = int(p.fcy)
    p.cfg += [f"/** Timer0 CTC: {f / 1e6:g} MHz / 64 / (249 + 1) = 1 kHz tick (ATmega datasheet §15.7.2). */",
              "#define HAL_T0_OCR          (249U)"]
    return """
#include "hal_timer.h"
#include "hal_config.h"
#include <avr/interrupt.h>
#include <util/delay.h>

static volatile uint32_t s_ms = 0UL;

ISR(TIMER0_COMPA_vect)
{
    s_ms++;
}

void hal_timer_init(void)
{
    TCCR0A = (uint8_t)(1U << WGM01);                       /* CTC, TOP = OCR0A */
    OCR0A = (uint8_t)HAL_T0_OCR;
    TIMSK0 = (uint8_t)(1U << OCIE0A);
    TCCR0B = (uint8_t)((1U << CS01) | (1U << CS00));        /* clk/64 */
    sei();
}

uint32_t hal_millis(void)
{
    uint32_t v;
    uint8_t sreg = SREG;
    cli();
    v = s_ms;
    SREG = sreg;
    return v;
}

void hal_delay_ms(uint16_t ms)
{
    uint32_t t0 = hal_millis();
    while ((hal_millis() - t0) < (uint32_t)ms)
    {
    }
}

void hal_delay_us(uint16_t us)
{
    while (us > 0U)
    {
        _delay_us(1);
        us--;
    }
}
"""


def uart_c(p) -> str:
    init, wr, rd = [], [], []
    mega = p.b["mcu"] == "ATMEGA2560"
    for n, u in sorted(p.uarts.items()):
        n = int(n)
        if n > (3 if mega else 0):
            continue
        baud = int(u.get("baud", 115200))
        val, u2x, err = ubrr(p.fcy, baud)
        p.cfg += [f"/** USART{n}: {baud} baud, UBRR{n} = {val}, U2X{n} = {u2x}, error {err:+.2f} % (datasheet baud table). */",
                  f"#define HAL_UBRR{n}           ({val}U)", f"#define HAL_U2X{n}            ({u2x}U)"]
        if abs(err) > 2.5:
            p.notes.append(f"USART{n}: {baud} baud has {err:+.1f} % error at {p.fcy / 1e6:g} MHz - pick a lower baud rate.")
        init.append(f"""    case HAL_UART_{n}:
        UBRR{n} = (uint16_t)HAL_UBRR{n};
        UCSR{n}A = (uint8_t)(HAL_U2X{n} << U2X{n});
        UCSR{n}C = (uint8_t)((1U << UCSZ{n}1) | (1U << UCSZ{n}0));   /* 8N1 */
        UCSR{n}B = (uint8_t)((1U << RXEN{n}) | (1U << TXEN{n}));
        break;""")
        wr.append(f"""    case HAL_UART_{n}:
        while ((UCSR{n}A & (1U << UDRE{n})) == 0U) {{ }}
        UDR{n} = byte;
        break;""")
        rd.append(f"""    case HAL_UART_{n}:
        if ((UCSR{n}A & (1U << RXC{n})) == 0U) {{ return HAL_ERR_BUSY; }}
        *out = UDR{n};
        break;""")
    return f"""
#include "hal_uart.h"
#include "hal_config.h"

hal_status_t hal_uart_init(hal_uart_id_t id)
{{
    switch (id)
    {{
{j(init)}
    default:
        return HAL_ERR_PARAM;
    }}
    return HAL_OK;
}}

hal_status_t hal_uart_write_byte(hal_uart_id_t id, uint8_t byte)
{{
    switch (id)
    {{
{j(wr)}
    default:
        return HAL_ERR_PARAM;
    }}
    return HAL_OK;
}}

hal_status_t hal_uart_read_byte(hal_uart_id_t id, uint8_t *out)
{{
    if (out == NULL) {{ return HAL_ERR_PARAM; }}
    switch (id)
    {{
{j(rd)}
    default:
        return HAL_ERR_PARAM;
    }}
    return HAL_OK;
}}
""" + UART_COMMON


def i2c_c(p) -> str:
    twbr = int((p.fcy / 100_000 - 16) / 2)
    p.cfg += [f"/** TWI: SCL = F_CPU / (16 + 2 * TWBR * 1) = {p.fcy / (16 + 2 * twbr) / 1000:.1f} kHz (datasheet §22.5.2). */",
              f"#define HAL_TWBR            ({twbr}U)", "#define HAL_TWI_TIMEOUT     (20000UL)   /**< polls of TWINT */"]
    return """
#include "hal_i2c.h"
#include "hal_config.h"

#define TW_STATUS_MASK      (0xF8U)
#define TW_START            (0x08U)
#define TW_REP_START        (0x10U)
#define TW_MT_SLA_ACK       (0x18U)
#define TW_MT_DATA_ACK      (0x28U)
#define TW_MR_SLA_ACK       (0x40U)

static hal_status_t twi_cmd(uint8_t twcr)
{
    uint32_t n = HAL_TWI_TIMEOUT;
    TWCR = twcr;
    while ((TWCR & (1U << TWINT)) == 0U)
    {
        n--;
        if (n == 0UL) { return HAL_ERR_TIMEOUT; }
    }
    return HAL_OK;
}

static void twi_stop(void)
{
    TWCR = (uint8_t)((1U << TWINT) | (1U << TWSTO) | (1U << TWEN));
}

static hal_status_t twi_start(uint8_t sla)
{
    uint8_t s;
    if (twi_cmd((uint8_t)((1U << TWINT) | (1U << TWSTA) | (1U << TWEN))) != HAL_OK) { return HAL_ERR_TIMEOUT; }
    s = (uint8_t)(TWSR & TW_STATUS_MASK);
    if ((s != TW_START) && (s != TW_REP_START)) { return HAL_ERR_DEVICE; }
    TWDR = sla;
    if (twi_cmd((uint8_t)((1U << TWINT) | (1U << TWEN))) != HAL_OK) { return HAL_ERR_TIMEOUT; }
    s = (uint8_t)(TWSR & TW_STATUS_MASK);
    return ((s == TW_MT_SLA_ACK) || (s == TW_MR_SLA_ACK)) ? HAL_OK : HAL_ERR_NACK;
}

hal_status_t hal_i2c_init(hal_i2c_id_t id)
{
    if (id >= HAL_I2C_COUNT) { return HAL_ERR_PARAM; }
    TWSR = 0U;                       /* prescaler 1 */
    TWBR = (uint8_t)HAL_TWBR;
    TWCR = (uint8_t)(1U << TWEN);
    return HAL_OK;
}

hal_status_t hal_i2c_write_read(hal_i2c_id_t id, uint8_t addr, const uint8_t *wr, size_t wlen, uint8_t *rd, size_t rlen)
{
    hal_status_t st = HAL_OK;
    size_t i;
    if (id >= HAL_I2C_COUNT) { return HAL_ERR_PARAM; }
    if (wlen > 0U)
    {
        st = twi_start((uint8_t)(addr << 1));
        for (i = 0U; (i < wlen) && (st == HAL_OK); i++)
        {
            TWDR = wr[i];
            st = twi_cmd((uint8_t)((1U << TWINT) | (1U << TWEN)));
            if ((st == HAL_OK) && ((TWSR & TW_STATUS_MASK) != TW_MT_DATA_ACK)) { st = HAL_ERR_NACK; }
        }
    }
    if ((st == HAL_OK) && (rlen > 0U))
    {
        st = twi_start((uint8_t)((addr << 1) | 1U));
        for (i = 0U; (i < rlen) && (st == HAL_OK); i++)
        {
            st = twi_cmd((uint8_t)((1U << TWINT) | (1U << TWEN) | ((i + 1U < rlen) ? (1U << TWEA) : 0U)));
            rd[i] = TWDR;
        }
    }
    twi_stop();
    return st;
}
""" + _i2c_common()


def _i2c_common():
    from .project import I2C_COMMON
    return I2C_COMMON


def spi_c(p) -> str:
    sp = SPI_PINS[p.b["mcu"]]
    mode = int(next(iter(p.spis.values()), {}).get("mode", 0))
    p.cfg += ["/** SPI master: SCK = F_CPU / 16 = 1 MHz, mode " + str(mode) + " (datasheet §19.5.1). */",
              f"#define HAL_SPI_MODE        ({mode}U)"]
    return f"""
#include "hal_spi.h"
#include "hal_config.h"

hal_status_t hal_spi_init(hal_spi_id_t id)
{{
    if (id >= HAL_SPI_COUNT) {{ return HAL_ERR_PARAM; }}
    /* SS must be an output in master mode, else a low level on it drops the SPI to slave (§19.3.2). */
    DDRB |= (uint8_t)((1U << DDB{sp['ss']}) | (1U << DDB{sp['sck']}) | (1U << DDB{sp['mosi']}));
    PORTB |= (uint8_t)(1U << PORTB{sp['ss']});
    SPCR = (uint8_t)((1U << SPE) | (1U << MSTR) | (1U << SPR0) | ((HAL_SPI_MODE & 3U) << CPHA));
    return HAL_OK;
}}

uint8_t hal_spi_transfer(hal_spi_id_t id, uint8_t tx)
{{
    (void)id;
    SPDR = tx;
    while ((SPSR & (1U << SPIF)) == 0U) {{ }}
    return SPDR;
}}
""" + SPI_COMMON


def adc_c(p) -> str:
    mega = p.b["mcu"] == "ATMEGA2560"
    p.cfg += ["/** ADC: AVcc reference, clk/128 = 125 kHz (datasheet §24.4: 50-200 kHz for 10 bits). */"]
    mux5 = """    if (channel > 7U) { ADCSRB |= (uint8_t)(1U << MUX5); }
    else              { ADCSRB &= (uint8_t)~(1U << MUX5); }
""" if mega else ""
    top = 15 if mega else 7
    return f"""
#include "hal_adc.h"
#include "hal_config.h"

hal_status_t hal_adc_init(void)
{{
    ADMUX = (uint8_t)(1U << REFS0);
    ADCSRA = (uint8_t)((1U << ADEN) | (1U << ADPS2) | (1U << ADPS1) | (1U << ADPS0));
    return HAL_OK;
}}

hal_status_t hal_adc_read(uint8_t channel, uint16_t *out)
{{
    uint16_t n = 0U;
    if ((out == NULL) || (channel > {top}U)) {{ return HAL_ERR_PARAM; }}
{mux5}    ADMUX = (uint8_t)((1U << REFS0) | (channel & 7U));
    ADCSRA |= (uint8_t)(1U << ADSC);
    while ((ADCSRA & (1U << ADSC)) != 0U)
    {{
        n++;
        if (n == 0xFFFFU) {{ return HAL_ERR_TIMEOUT; }}
    }}
    *out = ADC;
    return HAL_OK;
}}
"""


def pwm_c(p) -> str:
    init, set_us, set_pm, timers = [], [], [], {}
    for pw in p.pwms:
        m = __import__("re").fullmatch(r"OC(\d)([A-C])", pw["fn"])
        if not m:
            continue
        t, ch = int(m.group(1)), m.group(2)
        b = pw["bind"]
        hz = 50.0 if b["drv"] == "servo" else float(b["params"].get("hz", 2000 if b["drv"] == "tone" else 1000))
        nm = pw["name"]
        if t == 2:
            if b["drv"] == "servo":
                p.notes.append(f"{nm}: Timer2 is 8-bit - fast PWM at 976 Hz only; move the servo to OC1A/OC1B "
                               "(D9/D10 on Uno/Nano, D11/D12 on the Mega) for a 50 Hz servo signal.")
            p.cfg.append(f"/** {nm}: Timer2 fast PWM, clk/64 -> {p.fcy / 64 / 256:.0f} Hz, 4 us per tick. */")
            timers.setdefault(2, ["    TCCR2A |= (uint8_t)((1U << WGM21) | (1U << WGM20));",
                                  "    TCCR2B = (uint8_t)(1U << CS22);   /* clk/64 */"])
            init.append(f"    TCCR2A |= (uint8_t)(1U << COM2{ch}1);")
            set_us.append(f"    case HAL_PWM_{nm}:\n        ticks = (uint32_t)high_us / 4UL;\n        if (ticks > 255UL) {{ return HAL_ERR_PARAM; }}\n"
                          f"        OCR2{ch} = (uint8_t)ticks;\n        break;")
            set_pm.append(f"    case HAL_PWM_{nm}:\n        OCR2{ch} = (uint8_t)((255UL * permille) / 1000UL);\n        break;")
            continue
        top = int(round(p.fcy / 8 / hz)) - 1
        if t not in timers:
            p.cfg += [f"/** Timer{t}: fast PWM mode 14 (TOP = ICR{t}), clk/8 -> {p.fcy / 8 / (top + 1):g} Hz, "
                      f"{p.fcy / 8e6:g} ticks per us. */", f"#define HAL_ICR{t}            ({top}U)"]
            timers[t] = [f"    TCCR{t}A |= (uint8_t)(1U << WGM{t}1);",
                         f"    TCCR{t}B = (uint8_t)((1U << WGM{t}3) | (1U << WGM{t}2) | (1U << CS{t}1));",
                         f"    ICR{t} = (uint16_t)HAL_ICR{t};"]
        tpus = int(p.fcy / 8e6)
        init.append(f"    TCCR{t}A |= (uint8_t)(1U << COM{t}{ch}1);")
        set_us.append(f"    case HAL_PWM_{nm}:\n        ticks = (uint32_t)high_us * {tpus}UL;\n        if (ticks > HAL_ICR{t}) {{ return HAL_ERR_PARAM; }}\n"
                      f"        OCR{t}{ch} = (uint16_t)ticks;\n        break;")
        set_pm.append(f"    case HAL_PWM_{nm}:\n        OCR{t}{ch} = (uint16_t)(((uint32_t)HAL_ICR{t} * permille) / 1000UL);\n        break;")
    pre = [x for t in sorted(timers) for x in timers[t]]
    return f"""
#include "hal_pwm.h"
#include "hal_config.h"
#include <avr/io.h>

hal_status_t hal_pwm_init(void)
{{
{j(pre + init)}
    return HAL_OK;
}}

hal_status_t hal_pwm_set_us(hal_pwm_id_t ch, uint16_t high_us)
{{
    uint32_t ticks;
    switch (ch)
    {{
{j(set_us)}
    default:
        return HAL_ERR_PARAM;
    }}
    return HAL_OK;
}}

hal_status_t hal_pwm_set_permille(hal_pwm_id_t ch, uint16_t permille)
{{
    if (permille > 1000U) {{ return HAL_ERR_PARAM; }}
    switch (ch)
    {{
{j(set_pm)}
    default:
        return HAL_ERR_PARAM;
    }}
    return HAL_OK;
}}
"""
