"""Device-family back ends: clock, config bits and the peripheral half of the HAL.

full    PIC24F GB/GA1-class (OCxCON1 output compare) and PIC18FxxK22: GPIO, 1 ms tick, UART, I2C, SPI, ADC, PWM.
basic   every other device with a pin table: GPIO + software tick; other modules return HAL_ERR_UNSUPPORTED.
"""
from __future__ import annotations

from .. import uart as uartmod, spi as spimod, i2c as i2cmod, adc as adcmod, pwm as pwmmod, timers

CLOCK = {
    # device: (fosc_hz, fcy_hz, pragma overrides, clock statements, description)
    "PIC24FJ256GB110": (32e6, 16e6, {}, ["CLKDIVbits.RCDIV = 0;   /* FRC postscaler 1:1 -> 8 MHz into the 96 MHz PLL */"],
                        "FRC 8 MHz + PLL, Fosc 32 MHz, Fcy 16 MHz (verified board profile)"),
    "PIC24FJ64GA002": (32e6, 16e6, {"FNOSC": "FRCPLL", "FWDTEN": "OFF"}, ["CLKDIVbits.RCDIV = 0;   /* FRC 8 MHz into the 4x PLL */"],
                       "FRC 8 MHz x4 PLL, Fosc 32 MHz, Fcy 16 MHz"),
    "PIC18F45K22": (16e6, 4e6, {"FOSC": "INTIO67", "LVP": "OFF", "PBADEN": "OFF", "WDTEN": "OFF"},
                    ["OSCCONbits.IRCF = 7U;   /* HFINTOSC 16 MHz */"], "HFINTOSC 16 MHz, Fcy 4 MHz"),
    "PIC16F877A": (20e6, 5e6, {"FOSC": "HS", "LVP": "OFF", "WDTE": "OFF"}, [], "20 MHz HS crystal (assumed), Fcy 5 MHz"),
    "PIC16F18877": (32e6, 8e6, {"RSTOSC": "HFINT32", "FEXTOSC": "OFF", "LVP": "OFF", "WDTE": "OFF"}, [],
                    "HFINTOSC 32 MHz, Fcy 8 MHz"),
    "DSPIC33CK256MP508": (8e6, 4e6, {"FNOSC": "FRC", "FWDTEN": "ON_SW"}, [], "FRC 8 MHz, Fcy 4 MHz"),
    "PIC32MX795F512L": (8e6, 8e6, {"FNOSC": "FRC", "FPBDIV": "DIV_1", "FWDTEN": "OFF"}, [],
                        "FRC 8 MHz, SYSCLK = PBCLK 8 MHz, core timer 4 MHz"),
}
DEFAULT_CLOCK = (8e6, 4e6, {}, [], "default oscillator (check config_bits.c)")


def clock(device: str):
    return CLOCK.get(device.upper().replace("DSPIC", "DSPIC"), CLOCK.get(device, DEFAULT_CLOCK))


def kind(dev) -> str:
    s = dev.sfrs if dev else {}
    if dev and dev.compiler == "xc16" and "OC1CON1" in s and "I2C1CON" in s and "SPI1CON1" in s and "AD1CON1" in s and "RPOR0" in s:
        return "pic24"
    if dev and dev.compiler == "xc8" and "SSP1CON1" in s and "TXSTA1" in s and "ANSELC" in s and "T0CON" in s:
        return "pic18"
    return "basic"


def reg_t(dev) -> str:
    return {"xc8": "uint8_t", "xc16": "uint16_t", "xc32": "uint32_t"}.get(dev.compiler if dev else "xc16", "uint16_t")


INC = {"xc8": "#include <xc.h>", "xc16": "#include <xc.h>", "xc32": "#include <xc.h>"}

# --------------------------------------------------------------------------- timer
def timer_c(k, dev, fcy, fosc, cfg):
    if k == "pic24":
        sol = timers.solve(timers.TimerSpec(timer=1, period_s=1e-3, fosc_hz=fosc, interrupt=True, priority=4))
        cfg += [f"/** Timer1 period register for a 1 ms tick (Fcy {fcy/1e6:g} MHz, 1:{sol.prescale}). */",
                f"#define HAL_T1_PR           ({sol.period_reg}U)", f"#define HAL_T1_TCKPS        ({sol.tckps}U)",
                "#define HAL_TICK_IPL        (4U)   /**< Timer1 interrupt priority */"]
        return f"""
{INC['xc16']}
#include <libpic30.h>
#include "hal_timer.h"
#include "hal_config.h"

static volatile uint32_t s_ms = 0UL;

void hal_timer_init(void)
{{
    T1CON = 0;
    TMR1 = 0;
    PR1 = HAL_T1_PR;
    T1CONbits.TCKPS = HAL_T1_TCKPS;
    IPC0bits.T1IP = HAL_TICK_IPL;
    IFS0bits.T1IF = 0;
    IEC0bits.T1IE = 1;
    T1CONbits.TON = 1;
}}

/** @brief 1 ms tick (Timer1). */
void __attribute__((interrupt, no_auto_psv)) _T1Interrupt(void)
{{
    s_ms++;
    IFS0bits.T1IF = 0;
}}

uint32_t hal_millis(void)
{{
    uint32_t v;
    IEC0bits.T1IE = 0;   /* 32-bit read is not atomic on a 16-bit core */
    v = s_ms;
    IEC0bits.T1IE = 1;
    return v;
}}

void hal_delay_ms(uint16_t ms)
{{
    while (ms > 0U)
    {{
        __delay_ms(1);
        ms--;
    }}
}}

void hal_delay_us(uint16_t us)
{{
    __delay32(((uint32_t)us * (HAL_FCY_HZ / 1000000UL)));
}}
"""
    if k == "pic18":
        ticks = int(fcy / 1000)
        cfg += [f"/** Timer0 16-bit reload for a 1 ms tick at Fcy {fcy/1e6:g} MHz, no prescaler. */",
                f"#define HAL_T0_RELOAD       ({65536 - ticks}U)",
                "#define HAL_T0CON_16BIT_ON  (0x88U)   /**< TMR0ON | 16-bit | PSA (no prescaler) */"]
        return f"""
{INC['xc8']}
#include "hal_timer.h"
#include "hal_config.h"

static volatile uint32_t s_ms = 0UL;

void hal_timer_init(void)
{{
    T0CON = 0x00U;
    TMR0H = (uint8_t)(HAL_T0_RELOAD >> 8);
    TMR0L = (uint8_t)HAL_T0_RELOAD;
    INTCONbits.TMR0IF = 0;
    INTCONbits.TMR0IE = 1;
    INTCONbits.PEIE = 1;
    INTCONbits.GIE = 1;
    T0CON = HAL_T0CON_16BIT_ON;
}}

/** @brief Single high-priority vector: 1 ms tick (Timer0). */
void __interrupt() hal_isr(void)
{{
    if (INTCONbits.TMR0IF != 0U)
    {{
        TMR0H = (uint8_t)(HAL_T0_RELOAD >> 8);
        TMR0L = (uint8_t)HAL_T0_RELOAD;
        INTCONbits.TMR0IF = 0;
        s_ms++;
    }}
}}

uint32_t hal_millis(void)
{{
    uint32_t v;
    INTCONbits.TMR0IE = 0;
    v = s_ms;
    INTCONbits.TMR0IE = 1;
    return v;
}}

void hal_delay_ms(uint16_t ms)
{{
    while (ms > 0U)
    {{
        __delay_ms(1);
        ms--;
    }}
}}

void hal_delay_us(uint16_t us)
{{
    /* __delay_us() needs a constant: 4 us steps keep the loop overhead (~2 us at 4 MIPS) inside each step. */
    while (us >= 4U)
    {{
        __delay_us(2);
        us -= 4U;
    }}
}}
"""
    comp = dev.compiler if dev else "xc16"
    if comp == "xc32":
        delay = """
static void core_wait(uint32_t ticks)
{
    uint32_t start = _CP0_GET_COUNT();
    while ((uint32_t)(_CP0_GET_COUNT() - start) < ticks) { }
}

void hal_delay_ms(uint16_t ms)
{
    while (ms > 0U) { core_wait(HAL_CORE_TICKS_PER_US * 1000UL); s_ms++; ms--; }
}

void hal_delay_us(uint16_t us)
{
    core_wait(HAL_CORE_TICKS_PER_US * (uint32_t)us);
}
"""
        cfg += ["#define HAL_CORE_TICKS_PER_US  (HAL_FCY_HZ / 2000000UL)   /**< core timer runs at SYSCLK/2 */"]
        head = f"{INC['xc32']}\n"
    else:
        head = f"{INC[comp]}\n" + ("#include <libpic30.h>\n" if comp == "xc16" else "")
        us = ("    __delay32(((uint32_t)us * (HAL_FCY_HZ / 1000000UL)));" if comp == "xc16" else
              "    while (us >= 4U) { __delay_us(2); us -= 4U; }   /* approximate: __delay_us() needs a constant */")
        delay = f"""
void hal_delay_ms(uint16_t ms)
{{
    while (ms > 0U) {{ __delay_ms(1); s_ms++; ms--; }}
}}

void hal_delay_us(uint16_t us)
{{
{us}
}}
"""
    return head + """#include "hal_timer.h"
#include "hal_config.h"

/* Software tick: hal_millis() advances inside hal_delay_ms() only (no timer interrupt generated for this family). */
static uint32_t s_ms = 0UL;

void hal_timer_init(void) { s_ms = 0UL; }
uint32_t hal_millis(void) { return s_ms; }
""" + delay


def stub_c(mod, ids_enum, funcs, dev):
    """Unsupported module: every function returns HAL_ERR_UNSUPPORTED."""
    body = [f"#include \"hal_{mod}.h\"", "", f"/* hal_{mod} is not generated for {dev.name if dev else 'this device'} yet; "
            "functions report HAL_ERR_UNSUPPORTED so the project still builds. */", ""]
    for sig, ret in funcs:
        args = [a.strip().split()[-1].lstrip("*") for a in sig[sig.index("(") + 1:-1].split(",") if a.strip() not in ("void", "")]
        body += [sig, "{"] + [f"    (void){a};" for a in args] + [f"    return {ret};", "}", ""]
    return "\n".join(body)


STUBS = {
    "uart": [("hal_status_t hal_uart_init(hal_uart_id_t id)", "HAL_ERR_UNSUPPORTED"),
             ("hal_status_t hal_uart_write_byte(hal_uart_id_t id, uint8_t byte)", "HAL_ERR_UNSUPPORTED"),
             ("hal_status_t hal_uart_write_str(hal_uart_id_t id, const char *str)", "HAL_ERR_UNSUPPORTED"),
             ("hal_status_t hal_uart_read_byte(hal_uart_id_t id, uint8_t *out)", "HAL_ERR_UNSUPPORTED"),
             ("hal_status_t hal_uart_write_int(hal_uart_id_t id, int32_t value)", "HAL_ERR_UNSUPPORTED")],
    "i2c": [("hal_status_t hal_i2c_init(hal_i2c_id_t id)", "HAL_ERR_UNSUPPORTED"),
            ("hal_status_t hal_i2c_write(hal_i2c_id_t id, uint8_t addr, const uint8_t *data, size_t len)", "HAL_ERR_UNSUPPORTED"),
            ("hal_status_t hal_i2c_read(hal_i2c_id_t id, uint8_t addr, uint8_t *data, size_t len)", "HAL_ERR_UNSUPPORTED"),
            ("hal_status_t hal_i2c_write_read(hal_i2c_id_t id, uint8_t addr, const uint8_t *wr, size_t wlen, uint8_t *rd, size_t rlen)", "HAL_ERR_UNSUPPORTED")],
    "spi": [("hal_status_t hal_spi_init(hal_spi_id_t id)", "HAL_ERR_UNSUPPORTED"),
            ("uint8_t hal_spi_transfer(hal_spi_id_t id, uint8_t tx)", "0xFFU"),
            ("hal_status_t hal_spi_transfer_buf(hal_spi_id_t id, const uint8_t *tx, uint8_t *rx, size_t len)", "HAL_ERR_UNSUPPORTED")],
    "adc": [("hal_status_t hal_adc_init(void)", "HAL_ERR_UNSUPPORTED"),
            ("hal_status_t hal_adc_read(uint8_t channel, uint16_t *out)", "HAL_ERR_UNSUPPORTED")],
    "pwm": [("hal_status_t hal_pwm_init(void)", "HAL_ERR_UNSUPPORTED"),
            ("hal_status_t hal_pwm_set_us(hal_pwm_id_t ch, uint16_t high_us)", "HAL_ERR_UNSUPPORTED"),
            ("hal_status_t hal_pwm_set_permille(hal_pwm_id_t ch, uint16_t permille)", "HAL_ERR_UNSUPPORTED")],
}

UART_COMMON = """
hal_status_t hal_uart_write_str(hal_uart_id_t id, const char *str)
{
    hal_status_t st = HAL_OK;
    if (str == NULL) { return HAL_ERR_PARAM; }
    while ((*str != '\\0') && (st == HAL_OK))
    {
        st = hal_uart_write_byte(id, (uint8_t)*str);
        str++;
    }
    return st;
}

hal_status_t hal_uart_write_int(hal_uart_id_t id, int32_t value)
{
    char buf[11];
    uint8_t n = 0U;
    hal_status_t st = HAL_OK;
    uint32_t u = (value < 0) ? (uint32_t)(-value) : (uint32_t)value;
    if (value < 0) { st = hal_uart_write_byte(id, (uint8_t)'-'); }
    do { buf[n] = (char)('0' + (char)(u % 10U)); n++; u /= 10U; } while ((u != 0U) && (n < sizeof(buf)));
    while ((n > 0U) && (st == HAL_OK)) { n--; st = hal_uart_write_byte(id, (uint8_t)buf[n]); }
    return st;
}
"""

SPI_COMMON = """
hal_status_t hal_spi_transfer_buf(hal_spi_id_t id, const uint8_t *tx, uint8_t *rx, size_t len)
{
    size_t i;
    uint8_t b;
    if (id >= HAL_SPI_COUNT) { return HAL_ERR_PARAM; }
    for (i = 0U; i < len; i++)
    {
        b = hal_spi_transfer(id, (tx != NULL) ? tx[i] : 0xFFU);
        if (rx != NULL) { rx[i] = b; }
    }
    return HAL_OK;
}
"""

I2C_COMMON = """
#define I2C_TIMEOUT     (20000UL)   /**< busy-wait polls before HAL_ERR_TIMEOUT */

static hal_status_t i2c_wait(volatile hal_reg_t *reg, hal_reg_t mask, bool until_set)
{
    uint32_t n = 0UL;
    while (((*reg & mask) != 0U) != until_set)
    {
        n++;
        if (n > I2C_TIMEOUT) { return HAL_ERR_TIMEOUT; }
    }
    return HAL_OK;
}

hal_status_t hal_i2c_write(hal_i2c_id_t id, uint8_t addr, const uint8_t *data, size_t len)
{
    size_t i;
    hal_status_t st;
    if ((id >= HAL_I2C_COUNT) || ((data == NULL) && (len != 0U))) { return HAL_ERR_PARAM; }
    st = i2c_start(id, false);
    if (st == HAL_OK) { st = i2c_tx(id, (uint8_t)(addr << 1)); }
    for (i = 0U; (i < len) && (st == HAL_OK); i++) { st = i2c_tx(id, data[i]); }
    (void)i2c_stop(id);
    return st;
}

hal_status_t hal_i2c_read(hal_i2c_id_t id, uint8_t addr, uint8_t *data, size_t len)
{
    size_t i;
    hal_status_t st;
    if ((id >= HAL_I2C_COUNT) || (data == NULL) || (len == 0U)) { return HAL_ERR_PARAM; }
    st = i2c_start(id, false);
    if (st == HAL_OK) { st = i2c_tx(id, (uint8_t)((addr << 1) | 1U)); }
    for (i = 0U; (i < len) && (st == HAL_OK); i++) { st = i2c_rx(id, &data[i], (i + 1U) < len); }
    (void)i2c_stop(id);
    return st;
}

hal_status_t hal_i2c_write_read(hal_i2c_id_t id, uint8_t addr, const uint8_t *wr, size_t wlen, uint8_t *rd, size_t rlen)
{
    size_t i;
    hal_status_t st;
    if ((id >= HAL_I2C_COUNT) || (wr == NULL) || (rd == NULL) || (rlen == 0U)) { return HAL_ERR_PARAM; }
    st = i2c_start(id, false);
    if (st == HAL_OK) { st = i2c_tx(id, (uint8_t)(addr << 1)); }
    for (i = 0U; (i < wlen) && (st == HAL_OK); i++) { st = i2c_tx(id, wr[i]); }
    if (st == HAL_OK) { st = i2c_start(id, true); }
    if (st == HAL_OK) { st = i2c_tx(id, (uint8_t)((addr << 1) | 1U)); }
    for (i = 0U; (i < rlen) && (st == HAL_OK); i++) { st = i2c_rx(id, &rd[i], (i + 1U) < rlen); }
    (void)i2c_stop(id);
    return st;
}
"""


# --------------------------------------------------------------------------- PIC24 (GB110 class)
def pic24_uart(ports, fcy, cfg, ini, pps_in, pps_out, specs):
    """ports: {n: {'tx': rp, 'rx': rp, 'baud': b}}"""
    init, wr, rd = [], [], []
    for n, p in sorted(ports.items()):
        bs = uartmod.solve_baud(fcy, p["baud"])
        cfg += [f"/** UART{n}: {p['baud']} baud -> {bs.actual:.0f} baud ({bs.error_pct:+.2f} %), BRGH = {bs.brgh}. */",
                f"#define HAL_UART{n}_BRG      ({bs.brg}U)", f"#define HAL_UART{n}_BRGH     ({bs.brgh}U)"]
        if p.get("tx") is not None:
            f = uartmod.rpor_field(p["tx"])
            pps_out.append(f"    {f[0]}bits.{f[1]} = {uartmod.UART_TX_FN[n]}U;   /* RP{p['tx']} -> U{n}TX */")
        if p.get("rx") is not None:
            r = uartmod.UART_RX[n]
            pps_in.append(f"    {r[0]}bits.{r[1]} = {p['rx']}U;   /* U{n}RX <- RP{p['rx']} */")
        init.append(f"""    case HAL_UART_{n}:
        U{n}MODE = 0;
        U{n}MODEbits.BRGH = HAL_UART{n}_BRGH;
        U{n}BRG = HAL_UART{n}_BRG;
        U{n}MODEbits.UARTEN = 1;
        U{n}STAbits.UTXEN = 1;
        break;""")
        wr.append(f"""    case HAL_UART_{n}:
        while (U{n}STAbits.UTXBF != 0U) {{ }}
        U{n}TXREG = byte;
        break;""")
        rd.append(f"""    case HAL_UART_{n}:
        if (U{n}STAbits.OERR != 0U) {{ U{n}STAbits.OERR = 0; }}
        if (U{n}STAbits.URXDA == 0U) {{ return HAL_ERR_BUSY; }}
        *out = (uint8_t)U{n}RXREG;
        break;""")
        if p.get("tx") is not None and p.get("rx") is not None:
            specs.setdefault("uart", []).append(uartmod.UartSpec(baud=p["baud"], uart=n, tx_rp=p["tx"], rx_rp=p["rx"], fcy=fcy))
    return _uart_c("xc16", init, wr, rd)


def _uart_c(comp, init, wr, rd):
    j = "\n".join
    return f"""
{INC[comp]}
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


def pic24_i2c(mods, fcy, cfg, specs):
    regs, init = [], []
    for n in sorted(mods):
        sp = i2cmod.I2cSpec(n=n, freq_hz=100e3, fcy=fcy)
        _, dd = i2cmod.calc_lines(sp)
        cfg += [f"/** I2C{n}: 100 kHz, I2C{n}BRG from Eq. 16-1 (Fscl = {dd['fscl']/1e3:.1f} kHz). */",
                f"#define HAL_I2C{n}_BRG       ({dd['I2CxBRG']}U)"]
        regs.append(f"    {{ &I2C{n}CON, &I2C{n}STAT, &I2C{n}TRN, &I2C{n}RCV }},")
        init.append(f"""    case HAL_I2C_{n}:
        I2C{n}CON = 0;
        I2C{n}BRG = HAL_I2C{n}_BRG;
        I2C{n}CON = I2C_CON_ON_100K;
        break;""")
        specs.setdefault("i2c", []).append(sp)
    cfg += ["#define I2C_CON_ON_100K     (0x8200U)   /**< I2CEN | DISSLW (slew control off at 100 kHz) */"]
    j = "\n".join
    return f"""
{INC['xc16']}
#include "hal_i2c.h"
#include "hal_config.h"

#define I2C_SEN     (1U << 0)
#define I2C_RSEN    (1U << 1)
#define I2C_PEN     (1U << 2)
#define I2C_RCEN    (1U << 3)
#define I2C_ACKEN   (1U << 4)
#define I2C_ACKDT   (1U << 5)
#define I2C_TRSTAT  (1U << 14)
#define I2C_ACKSTAT (1U << 15)

typedef struct
{{
    volatile hal_reg_t       *con;
    volatile hal_reg_t       *stat;
    volatile hal_reg_t       *trn;
    volatile const hal_reg_t *rcv;
}} i2c_regs_t;

static const i2c_regs_t I2C_REGS[HAL_I2C_COUNT] = {{
{j(regs)}
}};
""" + I2C_COMMON.split("hal_status_t hal_i2c_write(")[0] + f"""
static hal_status_t i2c_start(hal_i2c_id_t id, bool repeated)
{{
    hal_reg_t bit = repeated ? I2C_RSEN : I2C_SEN;
    *I2C_REGS[id].con |= bit;
    return i2c_wait(I2C_REGS[id].con, bit, false);
}}

static hal_status_t i2c_stop(hal_i2c_id_t id)
{{
    *I2C_REGS[id].con |= I2C_PEN;
    return i2c_wait(I2C_REGS[id].con, I2C_PEN, false);
}}

static hal_status_t i2c_tx(hal_i2c_id_t id, uint8_t b)
{{
    hal_status_t st;
    *I2C_REGS[id].trn = b;
    st = i2c_wait(I2C_REGS[id].stat, I2C_TRSTAT, false);
    if ((st == HAL_OK) && ((*I2C_REGS[id].stat & I2C_ACKSTAT) != 0U)) {{ st = HAL_ERR_NACK; }}
    return st;
}}

static hal_status_t i2c_rx(hal_i2c_id_t id, uint8_t *b, bool ack)
{{
    hal_status_t st;
    *I2C_REGS[id].con |= I2C_RCEN;
    st = i2c_wait(I2C_REGS[id].con, I2C_RCEN, false);
    if (st != HAL_OK) {{ return st; }}
    *b = (uint8_t)*I2C_REGS[id].rcv;
    if (ack) {{ *I2C_REGS[id].con &= (hal_reg_t)~I2C_ACKDT; }}
    else     {{ *I2C_REGS[id].con |= I2C_ACKDT; }}
    *I2C_REGS[id].con |= I2C_ACKEN;
    return i2c_wait(I2C_REGS[id].con, I2C_ACKEN, false);
}}

hal_status_t hal_i2c_init(hal_i2c_id_t id)
{{
    switch (id)
    {{
{j(init)}
    default:
        return HAL_ERR_PARAM;
    }}
    return HAL_OK;
}}
""" + "hal_status_t hal_i2c_write(" + I2C_COMMON.split("hal_status_t hal_i2c_write(")[1]


def pic24_spi(buses, fcy, cfg, pps_in, pps_out, specs):
    """buses: {n: {'sck','sdo','sdi' rps, 'hz', 'mode'}}"""
    init, xfer = [], []
    for n, b in sorted(buses.items()):
        sp = spimod.SpiSpec(n=n, freq_hz=b["hz"], mode=b.get("mode", 0), fcy=fcy, sck_rp=b.get("sck"), sdo_rp=b.get("sdo"), sdi_rp=b.get("sdi"))
        _, dd = spimod.calc_lines(sp)
        cfg += [f"/** SPI{n}: master, mode {sp.mode}, {dd['freq']/1e3:g} kHz (requested {b['hz']/1e3:g} kHz). */",
                f"#define HAL_SPI{n}_CON1      (0x{dd['CON1']:04X}U)"]
        for key, nm in (("sck", "SCK"), ("sdo", "SDO")):
            if b.get(key) is not None:
                f = uartmod.rpor_field(b[key])
                pps_out.append(f"    {f[0]}bits.{f[1]} = {spimod.OUT_FN[n][nm]}U;   /* RP{b[key]} -> {nm}{n}{'OUT' if nm == 'SCK' else ''} */")
        if b.get("sdi") is not None:
            r = spimod.IN_REG[n]["SDI"]
            pps_in.append(f"    {r[0]}bits.{r[1]} = {b['sdi']}U;   /* SDI{n} <- RP{b['sdi']} */")
        init.append(f"""    case HAL_SPI_{n}:
        SPI{n}STAT = 0;
        SPI{n}CON1 = HAL_SPI{n}_CON1;
        SPI{n}CON2 = 0;
        SPI{n}STATbits.SPIEN = 1;
        break;""")
        xfer.append(f"""    case HAL_SPI_{n}:
        SPI{n}BUF = tx;
        while ((SPI{n}STATbits.SPIRBF == 0U) && (n < SPI_TIMEOUT)) {{ n++; }}
        rx = (uint8_t)SPI{n}BUF;
        break;""")
        if None not in (sp.sck_rp, sp.sdo_rp, sp.sdi_rp):
            specs.setdefault("spi", []).append(sp)
    j = "\n".join
    return f"""
{INC['xc16']}
#include "hal_spi.h"
#include "hal_config.h"

#define SPI_TIMEOUT     (10000UL)

hal_status_t hal_spi_init(hal_spi_id_t id)
{{
    switch (id)
    {{
{j(init)}
    default:
        return HAL_ERR_PARAM;
    }}
    return HAL_OK;
}}

uint8_t hal_spi_transfer(hal_spi_id_t id, uint8_t tx)
{{
    uint32_t n = 0UL;
    uint8_t rx = 0xFFU;
    switch (id)
    {{
{j(xfer)}
    default:
        break;
    }}
    return rx;
}}
""" + SPI_COMMON


def pic24_adc(channels, fcy, dev, cfg, specs):
    sp = adcmod.AdcSpec(channels=sorted(channels), fcy=fcy)
    _, dd = adcmod.calc_lines(sp)
    r = dd["registers"]
    pcfg = "AD1PCFGL" if "AD1PCFGL" in dev.sfrs else "AD1PCFG"
    cfg += [f"/** ADC: AN{', AN'.join(map(str, sorted(channels)))} analog, manual sample / auto convert. */",
            f"#define HAL_ADC_PCFG        (0x{r['AD1PCFGL']:04X}U)   /**< {pcfg}: 1 = digital */",
            f"#define HAL_ADC_CON1        (0x{r['AD1CON1'] & 0x7FFF:04X}U)",
            f"#define HAL_ADC_CON2        (0x{r['AD1CON2']:04X}U)",
            f"#define HAL_ADC_CON3        (0x{r['AD1CON3']:04X}U)"]
    specs["adc"] = sp
    return f"""
{INC['xc16']}
#include "hal_adc.h"
#include "hal_config.h"

#define ADC_TIMEOUT     (10000UL)
#define ADC_MAX_CH      (31U)

hal_status_t hal_adc_init(void)
{{
    {pcfg} = HAL_ADC_PCFG;
    AD1CON1 = HAL_ADC_CON1;
    AD1CON2 = HAL_ADC_CON2;
    AD1CON3 = HAL_ADC_CON3;
    AD1CHS = {sorted(channels)[0]}U;
    AD1CON1bits.ADON = 1;
    return HAL_OK;
}}

hal_status_t hal_adc_read(uint8_t channel, uint16_t *out)
{{
    uint32_t n = 0UL;
    if ((out == NULL) || (channel > ADC_MAX_CH)) {{ return HAL_ERR_PARAM; }}
    AD1CHS = channel;
    AD1CON1bits.SAMP = 1;
    while (AD1CON1bits.DONE == 0U)
    {{
        n++;
        if (n > ADC_TIMEOUT) {{ return HAL_ERR_TIMEOUT; }}
    }}
    *out = (uint16_t)ADC1BUF0;
    return HAL_OK;
}}
"""


def pic24_pwm(chans, fcy, cfg, pps_out, specs):
    """chans: [{'oc': n, 'rp': rp, 'hz': f, 'duty': d, 'name': str}]"""
    init, set_us, set_pm = [], [], []
    t2 = None
    for c in chans:
        n = c["oc"]
        sp = pwmmod.PwmSpec(oc=n, freq_hz=c["hz"], duty=c["duty"], fcy=fcy, pin_rp=c["rp"])
        _, dd = pwmmod.calc_lines(sp)
        pre = dd["prescale"]
        period = dd["ticks"]
        cfg += [f"/** OC{n} ({c['name']}): {dd['freq']:g} Hz, {'Timer2' if dd['mode'] == 'timer' else 'self-synchronised'}, "
                f"1:{pre}, {period} ticks per period. */",
                f"#define HAL_OC{n}_PERIOD     ({period}UL)", f"#define HAL_OC{n}_TICKS_US   ({fcy / 1e6 / pre:g}UL)   /**< timer ticks per us */",
                f"#define HAL_OC{n}_CON1       (0x{dd['OCxCON1']:04X}U)", f"#define HAL_OC{n}_CON2       (0x{dd['OCxCON2']:04X}U)",
                f"#define HAL_OC{n}_R_INIT     ({dd['OCxR']}U)"]
        f = uartmod.rpor_field(c["rp"])
        pps_out.append(f"    {f[0]}bits.{f[1]} = {pwmmod.PPS_CODE[n]}U;   /* RP{c['rp']} -> OC{n} */")
        lines = [f"    OC{n}CON1 = 0;", f"    OC{n}CON2 = HAL_OC{n}_CON2;"]
        if dd["mode"] == "timer":
            t2 = dd["timer_regs"]
        else:
            cfg.append(f"#define HAL_OC{n}_RS         ({dd['OCxRS']}U)")
            lines.append(f"    OC{n}RS = HAL_OC{n}_RS;")
        lines += [f"    OC{n}R = HAL_OC{n}_R_INIT;", f"    OC{n}CON1 = HAL_OC{n}_CON1;"]
        init += lines
        set_us.append(f"    case HAL_PWM_OC{n}:\n        ticks = (uint32_t)high_us * HAL_OC{n}_TICKS_US;\n"
                      f"        if (ticks > HAL_OC{n}_PERIOD) {{ return HAL_ERR_PARAM; }}\n        OC{n}R = (uint16_t)ticks;\n        break;")
        set_pm.append(f"    case HAL_PWM_OC{n}:\n        OC{n}R = (uint16_t)((HAL_OC{n}_PERIOD * permille) / PWM_PERMILLE_MAX);\n        break;")
        specs.setdefault("pwm", []).append(sp)
    pre = []
    if t2:
        cfg += [f"#define HAL_T2_PR           ({t2['PR2']}U)", f"#define HAL_T2_CON_ON       (0x{t2['T2CON']:04X}U)"]
        pre = ["    T2CON = 0;", "    TMR2 = 0;", "    PR2 = HAL_T2_PR;"]
        init.append("    T2CON = HAL_T2_CON_ON;")
    j = "\n".join
    return f"""
{INC['xc16']}
#include "hal_pwm.h"
#include "hal_config.h"

#define PWM_PERMILLE_MAX    (1000UL)

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
    if (permille > PWM_PERMILLE_MAX) {{ return HAL_ERR_PARAM; }}
    switch (ch)
    {{
{j(set_pm)}
    default:
        return HAL_ERR_PARAM;
    }}
    return HAL_OK;
}}
"""


# --------------------------------------------------------------------------- PIC18 K22
def pic18_uart(ports, fosc, cfg):
    init, wr, rd = [], [], []
    for n, p in sorted(ports.items()):
        brg = round(fosc / (4 * p["baud"])) - 1
        act = fosc / (4 * (brg + 1))
        cfg += [f"/** EUSART{n}: BRG16 = 1, BRGH = 1, {p['baud']} -> {act:.0f} baud ({(act / p['baud'] - 1) * 100:+.2f} %). */",
                f"#define HAL_UART{n}_SPBRG    ({brg}U)"]
        init.append(f"""    case HAL_UART_{n}:
        BAUDCON{n}bits.BRG16 = 1;
        SPBRGH{n} = (uint8_t)(HAL_UART{n}_SPBRG >> 8);
        SPBRG{n} = (uint8_t)HAL_UART{n}_SPBRG;
        TXSTA{n} = EUSART_TXSTA_ON;
        RCSTA{n} = EUSART_RCSTA_ON;
        break;""")
        wr.append(f"""    case HAL_UART_{n}:
        while (TXSTA{n}bits.TRMT == 0U) {{ }}
        TXREG{n} = byte;
        break;""")
        rd.append(f"""    case HAL_UART_{n}:
        if (RCSTA{n}bits.OERR != 0U) {{ RCSTA{n}bits.CREN = 0; RCSTA{n}bits.CREN = 1; }}
        if (PIR{1 if n == 1 else 3}bits.RC{n}IF == 0U) {{ return HAL_ERR_BUSY; }}
        *out = RCREG{n};
        break;""")
    cfg += ["#define EUSART_TXSTA_ON     (0x24U)   /**< TXEN | BRGH */",
            "#define EUSART_RCSTA_ON     (0x90U)   /**< SPEN | CREN */"]
    return _uart_c("xc8", init, wr, rd)


def pic18_i2c(mods, fosc, cfg):
    regs, init = [], []
    for n in sorted(mods):
        add = round(fosc / (4 * 100e3)) - 1
        cfg += [f"/** MSSP{n} I2C master 100 kHz: SSP{n}ADD = Fosc/(4*Fscl) - 1. */", f"#define HAL_I2C{n}_ADD       ({add}U)"]
        regs.append(f"    {{ &SSP{n}CON2, &SSP{n}STAT, &SSP{n}BUF, &PIR{1 if n == 1 else 3}, PIR_SSP{n}IF }},")
        init.append(f"""    case HAL_I2C_{n}:
        SSP{n}ADD = HAL_I2C{n}_ADD;
        SSP{n}STAT = SSP_STAT_SLEW_OFF;
        SSP{n}CON2 = 0x00U;
        SSP{n}CON1 = SSP_CON1_I2C_MASTER;
        break;""")
    cfg += ["#define SSP_STAT_SLEW_OFF   (0x80U)   /**< SMP: slew-rate control off (100 kHz) */",
            "#define SSP_CON1_I2C_MASTER (0x28U)   /**< SSPEN | I2C master, clock = Fosc/(4*(ADD+1)) */"]
    j = "\n".join
    return f"""
{INC['xc8']}
#include "hal_i2c.h"
#include "hal_config.h"

#define SSP_SEN     (1U << 0)
#define SSP_RSEN    (1U << 1)
#define SSP_PEN     (1U << 2)
#define SSP_RCEN    (1U << 3)
#define SSP_ACKEN   (1U << 4)
#define SSP_ACKDT   (1U << 5)
#define SSP_ACKSTAT (1U << 6)
#define PIR_SSP1IF  (1U << 3)   /* PIR1.SSP1IF */
#define PIR_SSP2IF  (1U << 7)   /* PIR3.SSP2IF */

typedef struct
{{
    volatile hal_reg_t *con2;
    volatile hal_reg_t *stat;
    volatile hal_reg_t *buf;
    volatile hal_reg_t *pir;
    hal_reg_t           ifmask;
}} i2c_regs_t;

static const i2c_regs_t I2C_REGS[HAL_I2C_COUNT] = {{
{j(regs)}
}};
""" + I2C_COMMON.split("hal_status_t hal_i2c_write(")[0] + f"""
static hal_status_t i2c_done(hal_i2c_id_t id)
{{
    hal_status_t st = i2c_wait(I2C_REGS[id].pir, I2C_REGS[id].ifmask, true);
    *I2C_REGS[id].pir &= (hal_reg_t)~I2C_REGS[id].ifmask;
    return st;
}}

static hal_status_t i2c_start(hal_i2c_id_t id, bool repeated)
{{
    *I2C_REGS[id].pir &= (hal_reg_t)~I2C_REGS[id].ifmask;
    *I2C_REGS[id].con2 |= repeated ? SSP_RSEN : SSP_SEN;
    return i2c_done(id);
}}

static hal_status_t i2c_stop(hal_i2c_id_t id)
{{
    *I2C_REGS[id].con2 |= SSP_PEN;
    return i2c_done(id);
}}

static hal_status_t i2c_tx(hal_i2c_id_t id, uint8_t b)
{{
    hal_status_t st;
    *I2C_REGS[id].buf = b;
    st = i2c_done(id);
    if ((st == HAL_OK) && ((*I2C_REGS[id].con2 & SSP_ACKSTAT) != 0U)) {{ st = HAL_ERR_NACK; }}
    return st;
}}

static hal_status_t i2c_rx(hal_i2c_id_t id, uint8_t *b, bool ack)
{{
    hal_status_t st;
    *I2C_REGS[id].con2 |= SSP_RCEN;
    st = i2c_done(id);
    if (st != HAL_OK) {{ return st; }}
    *b = *I2C_REGS[id].buf;
    if (ack) {{ *I2C_REGS[id].con2 &= (hal_reg_t)~SSP_ACKDT; }}
    else     {{ *I2C_REGS[id].con2 |= SSP_ACKDT; }}
    *I2C_REGS[id].con2 |= SSP_ACKEN;
    return i2c_done(id);
}}

hal_status_t hal_i2c_init(hal_i2c_id_t id)
{{
    switch (id)
    {{
{j(init)}
    default:
        return HAL_ERR_PARAM;
    }}
    return HAL_OK;
}}
""" + "hal_status_t hal_i2c_write(" + I2C_COMMON.split("hal_status_t hal_i2c_write(")[1]


def pic18_spi(buses, fosc, cfg):
    init, xfer = [], []
    for n, b in sorted(buses.items()):
        divs = [(4, 0), (16, 1), (64, 2)]
        div, ssmp = next(((d, v) for d, v in divs if fosc / d <= b["hz"]), divs[-1])
        ckp, cke = {0: (0, 1), 1: (0, 0), 2: (1, 1), 3: (1, 0)}[b.get("mode", 0)]
        cfg += [f"/** MSSP{n} SPI master, mode {b.get('mode', 0)}: Fosc/{div} = {fosc / div / 1e3:g} kHz (requested <= {b['hz']/1e3:g} kHz). */",
                f"#define HAL_SPI{n}_CON1      (0x{0x20 | (ckp << 4) | ssmp:02X}U)   /**< SSPEN | CKP={ckp} | SSPM */",
                f"#define HAL_SPI{n}_STAT      (0x{cke << 6:02X}U)   /**< CKE={cke} */"]
        init.append(f"""    case HAL_SPI_{n}:
        SSP{n}CON1 = 0x00U;
        SSP{n}STAT = HAL_SPI{n}_STAT;
        SSP{n}CON1 = HAL_SPI{n}_CON1;
        break;""")
        xfer.append(f"""    case HAL_SPI_{n}:
        SSP{n}BUF = tx;
        while ((SSP{n}STATbits.BF == 0U) && (n < SPI_TIMEOUT)) {{ n++; }}
        rx = SSP{n}BUF;
        break;""")
    j = "\n".join
    return f"""
{INC['xc8']}
#include "hal_spi.h"
#include "hal_config.h"

#define SPI_TIMEOUT     (10000UL)

hal_status_t hal_spi_init(hal_spi_id_t id)
{{
    switch (id)
    {{
{j(init)}
    default:
        return HAL_ERR_PARAM;
    }}
    return HAL_OK;
}}

uint8_t hal_spi_transfer(hal_spi_id_t id, uint8_t tx)
{{
    uint32_t n = 0UL;
    uint8_t rx = 0xFFU;
    switch (id)
    {{
{j(xfer)}
    default:
        break;
    }}
    return rx;
}}
""" + SPI_COMMON


def pic18_adc(cfg):
    cfg += ["#define HAL_ADCON2          (0xBEU)   /**< right-justified, 20 TAD, Fosc/64 (TAD 4 us at 16 MHz) */",
            "#define HAL_ADCON1          (0x00U)   /**< VREF+ = AVDD, VREF- = AVSS */"]
    return f"""
{INC['xc8']}
#include "hal_adc.h"
#include "hal_config.h"

#define ADC_TIMEOUT     (10000UL)
#define ADC_MAX_CH      (27U)

hal_status_t hal_adc_init(void)
{{
    ADCON2 = HAL_ADCON2;
    ADCON1 = HAL_ADCON1;
    ADCON0bits.ADON = 1;
    return HAL_OK;
}}

hal_status_t hal_adc_read(uint8_t channel, uint16_t *out)
{{
    uint32_t n = 0UL;
    if ((out == NULL) || (channel > ADC_MAX_CH)) {{ return HAL_ERR_PARAM; }}
    ADCON0bits.CHS = channel;
    ADCON0bits.GO_nDONE = 1;
    while (ADCON0bits.GO_nDONE != 0U)
    {{
        n++;
        if (n > ADC_TIMEOUT) {{ return HAL_ERR_TIMEOUT; }}
    }}
    *out = (uint16_t)(((uint16_t)ADRESH << 8) | ADRESL);
    return HAL_OK;
}}
"""


def pic18_pwm(chans, fosc, cfg, notes):
    hz = chans[0]["hz"]
    for c in chans[1:]:
        if c["hz"] != hz:
            notes.append(f"CCP PWM channels share Timer2: {c['name']} runs at {hz:g} Hz instead of {c['hz']:g} Hz.")
    pre, pr = next(((p, round(fosc / (4 * hz * p)) - 1) for p in (1, 4, 16) if round(fosc / (4 * hz * p)) - 1 <= 255), (16, 255))
    cfg += [f"/** Timer2 PWM time base: {fosc / (4 * pre * (pr + 1)):g} Hz (PR2 = {pr}, 1:{pre}). */",
            f"#define HAL_PR2             ({pr}U)", f"#define HAL_T2CON_ON        (0x{4 | {1: 0, 4: 1, 16: 2}[pre]:02X}U)   /**< TMR2ON | T2CKPS */",
            f"#define HAL_PWM_DUTY_MAX    ({4 * (pr + 1)}UL)   /**< 10-bit duty for 100 % */",
            f"#define HAL_PWM_TICKS_US    ({fosc / 1e6 / pre:g}UL)   /**< duty LSBs per us (Tosc * prescale) */"]
    init, setd = ["    PR2 = HAL_PR2;", "    T2CON = HAL_T2CON_ON;"], []
    for c in chans:
        n = c["ccp"]
        init += [f"    CCP{n}CON = CCP_PWM_MODE;", f"    CCPR{n}L = 0x00U;"]
        setd.append(f"    case HAL_PWM_CCP{n}:\n        CCPR{n}L = (uint8_t)(duty >> 2);\n"
                    f"        CCP{n}CON = (uint8_t)(CCP_PWM_MODE | (uint8_t)((duty & 0x03U) << 4));\n        break;")
    j = "\n".join
    return f"""
{INC['xc8']}
#include "hal_pwm.h"
#include "hal_config.h"

#define CCP_PWM_MODE        (0x0CU)
#define PWM_PERMILLE_MAX    (1000UL)

static hal_status_t set_duty(hal_pwm_id_t ch, uint16_t duty)
{{
    switch (ch)
    {{
{j(setd)}
    default:
        return HAL_ERR_PARAM;
    }}
    return HAL_OK;
}}

hal_status_t hal_pwm_init(void)
{{
{j(init)}
    return HAL_OK;
}}

hal_status_t hal_pwm_set_us(hal_pwm_id_t ch, uint16_t high_us)
{{
    uint32_t d = (uint32_t)high_us * HAL_PWM_TICKS_US;
    return (d > HAL_PWM_DUTY_MAX) ? HAL_ERR_PARAM : set_duty(ch, (uint16_t)d);
}}

hal_status_t hal_pwm_set_permille(hal_pwm_id_t ch, uint16_t permille)
{{
    if (permille > PWM_PERMILLE_MAX) {{ return HAL_ERR_PARAM; }}
    return set_duty(ch, (uint16_t)((HAL_PWM_DUTY_MAX * permille) / PWM_PERMILLE_MAX));
}}
"""
