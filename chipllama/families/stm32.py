"""STM32 F1 / F4 / F0 register-level HAL back end (CMSIS device headers, arm-none-eabi-gcc)."""
from __future__ import annotations

import re

from ..projgen.backends import UART_COMMON, SPI_COMMON

j = "\n".join


def _clk(p, inst: str) -> int:
    from .project import STM, APB2_SET
    s = STM[p.series]
    if inst.startswith("TIM"):
        return s["sys"]           # timer kernel clock = 2 x PCLK when the APB prescaler is not 1 = SYSCLK here
    return s["apb2"] if inst in APB2_SET else s["apb1"]


def rcc(p, inst: str) -> str:
    """RCC enable statement for a peripheral instance (checked against the CMSIS header bit list)."""
    from .project import APB2_SET
    if p.series == "f0":
        if inst == "ADC1":
            return "RCC->APB2ENR |= RCC_APB2ENR_ADCEN;"
        bus = "APB2" if inst in APB2_SET else "APB1"
    else:
        bus = "APB2" if inst in APB2_SET else "APB1"
    return f"RCC->{bus}ENR |= RCC_{bus}ENR_{inst}EN;"


def clock_c(p) -> str:
    s = p.series
    if s == "f1":
        body = """    uint32_t n = 0UL;
    RCC->CR |= RCC_CR_HSEON;                                   /* 8 MHz crystal on the Blue Pill */
    while (((RCC->CR & RCC_CR_HSERDY) == 0U) && (n < 0x50000UL)) { n++; }
    if ((RCC->CR & RCC_CR_HSERDY) == 0U) { return; }           /* no crystal: stay on HSI 8 MHz (timings will be off) */
    FLASH->ACR = FLASH_ACR_PRFTBE | FLASH_ACR_LATENCY_1;       /* 2 wait states for 48-72 MHz (RM0008 3.3.3) */
    RCC->CFGR = RCC_CFGR_PLLSRC | RCC_CFGR_PLLMULL9 | RCC_CFGR_PPRE1_DIV2 | RCC_CFGR_ADCPRE_DIV6;
    RCC->CR |= RCC_CR_PLLON;
    while ((RCC->CR & RCC_CR_PLLRDY) == 0U) { }
    RCC->CFGR |= RCC_CFGR_SW_PLL;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL) { }"""
    elif s == "f4":
        body = """    RCC->APB1ENR |= RCC_APB1ENR_PWREN;
    FLASH->ACR = FLASH_ACR_ICEN | FLASH_ACR_DCEN | FLASH_ACR_PRFTEN | FLASH_ACR_LATENCY_2WS;   /* RM0383 3.4.1 */
    RCC->PLLCFGR = (16UL << RCC_PLLCFGR_PLLM_Pos) | (336UL << RCC_PLLCFGR_PLLN_Pos) |
                   (1UL << RCC_PLLCFGR_PLLP_Pos) | (7UL << RCC_PLLCFGR_PLLQ_Pos);   /* HSI/16*336/4 = 84 MHz, PLLP=4 */
    RCC->CFGR = RCC_CFGR_PPRE1_DIV2;
    RCC->CR |= RCC_CR_PLLON;
    while ((RCC->CR & RCC_CR_PLLRDY) == 0U) { }
    RCC->CFGR |= RCC_CFGR_SW_PLL;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL) { }"""
    else:
        body = """    FLASH->ACR = FLASH_ACR_PRFTBE | FLASH_ACR_LATENCY;        /* 1 wait state above 24 MHz (RM0360 3.2) */
    RCC->CFGR = RCC_CFGR_PLLMUL12;                             /* PLLSRC = HSI/2, x12 = 48 MHz */
    RCC->CR |= RCC_CR_PLLON;
    while ((RCC->CR & RCC_CR_PLLRDY) == 0U) { }
    RCC->CFGR |= RCC_CFGR_SW_PLL;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL) { }"""
    return f"""
#include "hal.h"

/** @brief CMSIS: core clock in Hz after hal_clock_init(). */
uint32_t SystemCoreClock = {int(p.fcy)}UL;

/** @brief Called by the startup code before main(): nothing to do, board_init() sets the clock. */
void SystemInit(void)
{{
#if defined(__FPU_PRESENT) && (__FPU_PRESENT == 1U)
    SCB->CPACR |= (0xFUL << 20);   /* CP10/CP11 full access */
#endif
}}

void hal_clock_init(void)
{{
{body}
}}
"""


def timer_c(p) -> str:
    p.cfg += [f"/** SysTick: {int(p.fcy)} Hz / 1000 = 1 ms. */", f"#define HAL_SYSTICK_LOAD    ({int(p.fcy) // 1000}UL)",
              f"#define HAL_LOOPS_PER_US    ({max(1, int(p.fcy / 4e6))}UL)   /**< busy-loop iterations per us (approx.) */"]
    return """
#include "hal_timer.h"
#include "hal_config.h"

static volatile uint32_t s_ms = 0UL;

void SysTick_Handler(void);
void SysTick_Handler(void)
{
    s_ms++;
}

void hal_timer_init(void)
{
    (void)SysTick_Config(HAL_SYSTICK_LOAD);
}

uint32_t hal_millis(void)
{
    return s_ms;
}

void hal_delay_ms(uint16_t ms)
{
    uint32_t t0 = s_ms;
    while ((s_ms - t0) < (uint32_t)ms)
    {
    }
}

void hal_delay_us(uint16_t us)
{
    volatile uint32_t n = (uint32_t)us * HAL_LOOPS_PER_US;
    while (n > 0UL)
    {
        n--;
    }
}
"""


def uart_c(p) -> str:
    init, wr, rd = [], [], []
    f0 = p.series == "f0"
    for n, u in sorted(p.uarts.items()):
        inst = f"USART{int(n)}"
        baud = int(u.get("baud", 115200))
        clk = _clk(p, inst)
        brr = int(round(clk / baud))
        err = (clk / brr / baud - 1) * 100
        p.cfg += [f"/** {inst}: {baud} baud from PCLK {clk / 1e6:g} MHz, BRR = {brr} (0x{brr:04X}), error {err:+.2f} %. */",
                  f"#define HAL_{inst}_BRR      ({brr}U)"]
        sr, txe, rxne, tdr, rdr = (("ISR", "USART_ISR_TXE", "USART_ISR_RXNE", "TDR", "RDR") if f0 else
                                   ("SR", "USART_SR_TXE", "USART_SR_RXNE", "DR", "DR"))
        init.append(f"""    case HAL_UART_{int(n)}:
        {rcc(p, inst)}
        {inst}->CR1 = 0U;
        {inst}->BRR = HAL_{inst}_BRR;
        {inst}->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;   /* 8N1 */
        break;""")
        wr.append(f"""    case HAL_UART_{int(n)}:
        while (({inst}->{sr} & {txe}) == 0U) {{ }}
        {inst}->{tdr} = byte;
        break;""")
        rd.append(f"""    case HAL_UART_{int(n)}:
        if (({inst}->{sr} & {rxne}) == 0U) {{ return HAL_ERR_BUSY; }}
        *out = (uint8_t){inst}->{rdr};
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


def _inst_sw(p, ids, kind, fmt):
    return j(f"    case HAL_{kind}_{i}: return {fmt.format(i=i)};" for i in ids)


def i2c_c(p) -> str:
    from .project import I2C_COMMON
    ids = sorted(int(i) for i in p.i2cs)
    get = f"""static I2C_TypeDef *i2c_of(hal_i2c_id_t id)
{{
    switch (id)
    {{
{_inst_sw(p, ids, 'I2C', 'I2C{i}')}
    default: return NULL;
    }}
}}
"""
    en = j(f"    if (id == HAL_I2C_{i}) {{ {rcc(p, f'I2C{i}')} }}" for i in ids)
    if p.series == "f0":
        p.cfg += ["/** I2C1 (v2 IP): kernel clock HSI 8 MHz, TIMINGR 0x2000090E = 100 kHz (RM0360 Table 76). */",
                  "#define HAL_I2C_TIMINGR     (0x2000090EUL)", "#define HAL_I2C_TIMEOUT     (100000UL)"]
        return f"""
#include "hal_i2c.h"
#include "hal_config.h"

{get}
static hal_status_t wait_flag(I2C_TypeDef *i2c, uint32_t flag)
{{
    uint32_t n = HAL_I2C_TIMEOUT;
    while ((i2c->ISR & flag) == 0U)
    {{
        if ((i2c->ISR & I2C_ISR_NACKF) != 0U)
        {{
            i2c->ICR = I2C_ICR_NACKCF;
            i2c->CR2 |= I2C_CR2_STOP;
            return HAL_ERR_NACK;
        }}
        n--;
        if (n == 0UL) {{ return HAL_ERR_TIMEOUT; }}
    }}
    return HAL_OK;
}}

hal_status_t hal_i2c_init(hal_i2c_id_t id)
{{
    I2C_TypeDef *i2c = i2c_of(id);
    if (i2c == NULL) {{ return HAL_ERR_PARAM; }}
{en}
    i2c->CR1 = 0U;
    i2c->TIMINGR = HAL_I2C_TIMINGR;
    i2c->CR1 = I2C_CR1_PE;
    return HAL_OK;
}}

hal_status_t hal_i2c_write_read(hal_i2c_id_t id, uint8_t addr, const uint8_t *wr, size_t wlen, uint8_t *rd, size_t rlen)
{{
    I2C_TypeDef *i2c = i2c_of(id);
    hal_status_t st = HAL_OK;
    size_t i;
    if ((i2c == NULL) || (wlen > 255U) || (rlen > 255U)) {{ return HAL_ERR_PARAM; }}
    if (wlen > 0U)
    {{
        i2c->CR2 = ((uint32_t)addr << 1) | ((uint32_t)wlen << 16) | ((rlen == 0U) ? I2C_CR2_AUTOEND : 0U) | I2C_CR2_START;
        for (i = 0U; (i < wlen) && (st == HAL_OK); i++)
        {{
            st = wait_flag(i2c, I2C_ISR_TXIS);
            if (st == HAL_OK) {{ i2c->TXDR = wr[i]; }}
        }}
        if ((st == HAL_OK) && (rlen > 0U)) {{ st = wait_flag(i2c, I2C_ISR_TC); }}
    }}
    if ((st == HAL_OK) && (rlen > 0U))
    {{
        i2c->CR2 = ((uint32_t)addr << 1) | ((uint32_t)rlen << 16) | I2C_CR2_RD_WRN | I2C_CR2_AUTOEND | I2C_CR2_START;
        for (i = 0U; (i < rlen) && (st == HAL_OK); i++)
        {{
            st = wait_flag(i2c, I2C_ISR_RXNE);
            if (st == HAL_OK) {{ rd[i] = (uint8_t)i2c->RXDR; }}
        }}
    }}
    if (st == HAL_OK) {{ st = wait_flag(i2c, I2C_ISR_STOPF); }}
    i2c->ICR = I2C_ICR_STOPCF;
    return st;
}}
""" + I2C_COMMON
    pclk = _clk(p, "I2C1")
    mhz = pclk // 1_000_000
    ccr = pclk // (2 * 100_000)
    p.cfg += [f"/** I2C (legacy IP): PCLK1 {mhz} MHz, standard mode 100 kHz: CCR = PCLK1 / 200 kHz = {ccr}, "
              f"TRISE = {mhz + 1} (RM0008 26.6.8 / RM0383 18.6.8). */",
              f"#define HAL_I2C_FREQ        ({mhz}U)", f"#define HAL_I2C_CCR         ({ccr}U)", f"#define HAL_I2C_TRISE       ({mhz + 1}U)",
              "#define HAL_I2C_TIMEOUT     (100000UL)"]
    return f"""
#include "hal_i2c.h"
#include "hal_config.h"

{get}
static hal_status_t wait_sr1(I2C_TypeDef *i2c, uint32_t flag)
{{
    uint32_t n = HAL_I2C_TIMEOUT;
    while ((i2c->SR1 & flag) == 0U)
    {{
        if ((i2c->SR1 & I2C_SR1_AF) != 0U)
        {{
            i2c->SR1 &= ~I2C_SR1_AF;
            i2c->CR1 |= I2C_CR1_STOP;
            return HAL_ERR_NACK;
        }}
        n--;
        if (n == 0UL) {{ return HAL_ERR_TIMEOUT; }}
    }}
    return HAL_OK;
}}

static hal_status_t start(I2C_TypeDef *i2c, uint8_t sla, bool single)
{{
    hal_status_t st;
    volatile uint32_t tmp;
    i2c->CR1 |= I2C_CR1_START;
    st = wait_sr1(i2c, I2C_SR1_SB);
    if (st != HAL_OK) {{ return st; }}
    i2c->DR = sla;
    st = wait_sr1(i2c, I2C_SR1_ADDR);
    if (st != HAL_OK) {{ return st; }}
    if (single) {{ i2c->CR1 &= ~I2C_CR1_ACK; }}
    tmp = i2c->SR1;                       /* ADDR is cleared by reading SR1 then SR2 */
    tmp = i2c->SR2;
    (void)tmp;
    return HAL_OK;
}}

hal_status_t hal_i2c_init(hal_i2c_id_t id)
{{
    I2C_TypeDef *i2c = i2c_of(id);
    if (i2c == NULL) {{ return HAL_ERR_PARAM; }}
{en}
    i2c->CR1 = I2C_CR1_SWRST;
    i2c->CR1 = 0U;
    i2c->CR2 = HAL_I2C_FREQ;
    i2c->CCR = HAL_I2C_CCR;
    i2c->TRISE = HAL_I2C_TRISE;
    i2c->CR1 = I2C_CR1_PE;
    return HAL_OK;
}}

hal_status_t hal_i2c_write_read(hal_i2c_id_t id, uint8_t addr, const uint8_t *wr, size_t wlen, uint8_t *rd, size_t rlen)
{{
    I2C_TypeDef *i2c = i2c_of(id);
    hal_status_t st = HAL_OK;
    size_t i;
    if (i2c == NULL) {{ return HAL_ERR_PARAM; }}
    if (wlen > 0U)
    {{
        st = start(i2c, (uint8_t)(addr << 1), false);
        for (i = 0U; (i < wlen) && (st == HAL_OK); i++)
        {{
            st = wait_sr1(i2c, I2C_SR1_TXE);
            if (st == HAL_OK) {{ i2c->DR = wr[i]; }}
        }}
        if (st == HAL_OK) {{ st = wait_sr1(i2c, I2C_SR1_BTF); }}
    }}
    if ((st == HAL_OK) && (rlen > 0U))
    {{
        i2c->CR1 |= I2C_CR1_ACK;
        st = start(i2c, (uint8_t)((addr << 1) | 1U), rlen == 1U);
        for (i = 0U; (i < rlen) && (st == HAL_OK); i++)
        {{
            if (i + 1U == rlen) {{ i2c->CR1 &= ~I2C_CR1_ACK; i2c->CR1 |= I2C_CR1_STOP; }}
            st = wait_sr1(i2c, I2C_SR1_RXNE);
            if (st == HAL_OK) {{ rd[i] = (uint8_t)i2c->DR; }}
        }}
    }}
    else
    {{
        i2c->CR1 |= I2C_CR1_STOP;
    }}
    return st;
}}
""" + I2C_COMMON


def spi_c(p) -> str:
    ids = sorted(int(i) for i in p.spis)
    mode = int(next(iter(p.spis.values()), {}).get("mode", 0))
    clk = _clk(p, f"SPI{ids[0]}" if ids else "SPI1")
    br, div = next(((b, 2 << b) for b in range(8) if clk / (2 << b) <= 1.1e6), (7, 256))
    p.cfg += [f"/** SPI: PCLK {clk / 1e6:g} MHz / {div} = {clk / div / 1e3:g} kHz, mode {mode}. */",
              f"#define HAL_SPI_BR          ({br}U)", f"#define HAL_SPI_MODE        ({mode}U)"]
    f0 = p.series == "f0"
    en = j(f"    if (id == HAL_SPI_{i}) {{ {rcc(p, f'SPI{i}')} }}" for i in ids)
    dr8 = "*(volatile uint8_t *)&spi->DR" if f0 else "spi->DR"
    cr2 = "    spi->CR2 = (7UL << 8) | SPI_CR2_FRXTH;   /* 8-bit frames, RXNE at 8 bits */\n" if f0 else ""
    return f"""
#include "hal_spi.h"
#include "hal_config.h"

static SPI_TypeDef *spi_of(hal_spi_id_t id)
{{
    switch (id)
    {{
{_inst_sw(p, ids, 'SPI', 'SPI{i}')}
    default: return NULL;
    }}
}}

hal_status_t hal_spi_init(hal_spi_id_t id)
{{
    SPI_TypeDef *spi = spi_of(id);
    if (spi == NULL) {{ return HAL_ERR_PARAM; }}
{en}
    spi->CR1 = 0U;
{cr2}    spi->CR1 = SPI_CR1_MSTR | SPI_CR1_SSM | SPI_CR1_SSI | ((uint32_t)HAL_SPI_BR << 3) | (HAL_SPI_MODE & 3U);
    spi->CR1 |= SPI_CR1_SPE;
    return HAL_OK;
}}

uint8_t hal_spi_transfer(hal_spi_id_t id, uint8_t tx)
{{
    SPI_TypeDef *spi = spi_of(id);
    if (spi == NULL) {{ return 0xFFU; }}
    while ((spi->SR & SPI_SR_TXE) == 0U) {{ }}
    {dr8} = tx;
    while ((spi->SR & SPI_SR_RXNE) == 0U) {{ }}
    return (uint8_t){dr8};
}}
""" + SPI_COMMON


def adc_c(p) -> str:
    s = p.series
    if s == "f1":
        p.cfg += ["/** ADC1: ADCCLK = 72 MHz / 6 = 12 MHz (max 14), 239.5-cycle sampling (RM0008 11.12). */"]
        init = f"""    {rcc(p, 'ADC1')}
    ADC1->SMPR2 = 0x3FFFFFFFUL;            /* 239.5 cycles on ch 0-9 */
    ADC1->SMPR1 = 0x00FFFFFFUL;
    ADC1->CR2 = ADC_CR2_ADON;
    for (volatile uint32_t d = 0UL; d < 1000UL; d++) {{ }}
    ADC1->CR2 |= ADC_CR2_CAL;
    while ((ADC1->CR2 & ADC_CR2_CAL) != 0U) {{ }}"""
        read = """    ADC1->SQR3 = channel;
    ADC1->CR2 |= ADC_CR2_ADON;             /* F1: ADON again starts a conversion */
    while ((ADC1->SR & ADC_SR_EOC) == 0U) { if (++n == 0xFFFFFUL) { return HAL_ERR_TIMEOUT; } }
    *out = (uint16_t)ADC1->DR;"""
        top = 17
    elif s == "f4":
        p.cfg += ["/** ADC1: ADCCLK = PCLK2 84 MHz / 4 = 21 MHz, 480-cycle sampling. */"]
        init = f"""    {rcc(p, 'ADC1')}
    ADC1_COMMON->CCR = ADC_CCR_ADCPRE_0;   /* PCLK2 / 4 */
    ADC1->SMPR2 = 0x3FFFFFFFUL;
    ADC1->SMPR1 = 0x07FFFFFFUL;
    ADC1->CR2 = ADC_CR2_ADON;"""
        read = """    ADC1->SQR3 = channel;
    ADC1->CR2 |= ADC_CR2_SWSTART;
    while ((ADC1->SR & ADC_SR_EOC) == 0U) { if (++n == 0xFFFFFUL) { return HAL_ERR_TIMEOUT; } }
    *out = (uint16_t)ADC1->DR;"""
        top = 18
    else:
        p.cfg += ["/** ADC: clock PCLK / 4 = 12 MHz (max 14), 239.5-cycle sampling, self-calibrated (RM0360 13.4). */"]
        init = f"""    {rcc(p, 'ADC1')}
    ADC1->CFGR2 = ADC_CFGR2_CKMODE_1;      /* PCLK / 4 */
    ADC1->CR = ADC_CR_ADCAL;
    while ((ADC1->CR & ADC_CR_ADCAL) != 0U) {{ }}
    ADC1->SMPR = 7U;
    ADC1->CR = ADC_CR_ADEN;
    while ((ADC1->ISR & ADC_ISR_ADRDY) == 0U) {{ }}"""
        read = """    ADC1->CHSELR = 1UL << channel;
    ADC1->CR |= ADC_CR_ADSTART;
    while ((ADC1->ISR & ADC_ISR_EOC) == 0U) { if (++n == 0xFFFFFUL) { return HAL_ERR_TIMEOUT; } }
    *out = (uint16_t)ADC1->DR;"""
        top = 18
    return f"""
#include "hal_adc.h"
#include "hal_config.h"

hal_status_t hal_adc_init(void)
{{
{init}
    return HAL_OK;
}}

hal_status_t hal_adc_read(uint8_t channel, uint16_t *out)
{{
    uint32_t n = 0UL;
    if ((out == NULL) || (channel > {top}U)) {{ return HAL_ERR_PARAM; }}
{read}
    return HAL_OK;
}}
"""


def pwm_c(p) -> str:
    from .project import ADV_TIM
    init, set_us, set_pm, tims = [], [], [], {}
    for pw in p.pwms:
        m = re.fullmatch(r"(TIM\d+)_CH(\d)", pw["fn"])
        if not m:
            continue
        tim, ch = m.group(1), int(m.group(2))
        b = pw["bind"]
        hz = 50.0 if b["drv"] == "servo" else float(b["params"].get("hz", 2000 if b["drv"] == "tone" else 1000))
        clk = _clk(p, tim)
        nm = pw["name"]
        if tim not in tims:
            arr = int(round(1e6 / hz)) - 1
            tims[tim] = arr
            p.cfg += [f"/** {tim}: {clk / 1e6:g} MHz / (PSC + 1) = 1 MHz tick, ARR = {arr} -> {1e6 / (arr + 1):g} Hz. */",
                      f"#define HAL_{tim}_PSC       ({clk // 1_000_000 - 1}U)", f"#define HAL_{tim}_ARR       ({arr}U)"]
            init += [f"    {rcc(p, tim)}", f"    {tim}->PSC = HAL_{tim}_PSC;", f"    {tim}->ARR = HAL_{tim}_ARR;"]
        reg = "CCMR1" if ch <= 2 else "CCMR2"
        sh = 0 if ch % 2 else 8
        init += [f"    {tim}->{reg} |= (0x6UL << {4 + sh}) | (1UL << {3 + sh});   /* CH{ch}: PWM mode 1, preload */",
                 f"    {tim}->CCER |= (1UL << {4 * (ch - 1)});                   /* CC{ch}E */"]
        set_us.append(f"    case HAL_PWM_{nm}:\n        if (high_us > HAL_{tim}_ARR) {{ return HAL_ERR_PARAM; }}\n"
                      f"        {tim}->CCR{ch} = high_us;\n        break;")
        set_pm.append(f"    case HAL_PWM_{nm}:\n        {tim}->CCR{ch} = (uint32_t)(((uint32_t)(HAL_{tim}_ARR + 1U) * permille) / 1000UL);\n        break;")
    for tim in tims:
        if tim in ADV_TIM:
            init.append(f"    {tim}->BDTR |= TIM_BDTR_MOE;                        /* advanced timer: main output enable */")
        init += [f"    {tim}->EGR = TIM_EGR_UG;", f"    {tim}->CR1 = TIM_CR1_ARPE | TIM_CR1_CEN;"]
    return f"""
#include "hal_pwm.h"
#include "hal_config.h"

hal_status_t hal_pwm_init(void)
{{
{j(init)}
    return HAL_OK;
}}

hal_status_t hal_pwm_set_us(hal_pwm_id_t ch, uint16_t high_us)
{{
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


LINKER = """/* {mcu}: {flash} KB flash, {ram} KB RAM - generated by ChipLlama */
ENTRY(Reset_Handler)

MEMORY
{{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = {flash}K
    RAM   (xrw) : ORIGIN = 0x20000000, LENGTH = {ram}K
}}

_estack = ORIGIN(RAM) + LENGTH(RAM);
_Min_Heap_Size = 0x0;
_Min_Stack_Size = 0x200;

SECTIONS
{{
    .isr_vector : {{ . = ALIGN(4); KEEP(*(.isr_vector)) . = ALIGN(4); }} >FLASH
    .text :
    {{
        . = ALIGN(4);
        *(.text) *(.text*) *(.glue_7) *(.glue_7t) *(.eh_frame)
        KEEP(*(.init)) KEEP(*(.fini))
        *(.rodata) *(.rodata*)
        . = ALIGN(4);
        _etext = .;
    }} >FLASH
    .ARM.extab : {{ *(.ARM.extab* .gnu.linkonce.armextab.*) }} >FLASH
    .ARM : {{ __exidx_start = .; *(.ARM.exidx*) __exidx_end = .; }} >FLASH
    .preinit_array : {{ PROVIDE_HIDDEN(__preinit_array_start = .); KEEP(*(.preinit_array*)) PROVIDE_HIDDEN(__preinit_array_end = .); }} >FLASH
    .init_array : {{ PROVIDE_HIDDEN(__init_array_start = .); KEEP(*(SORT(.init_array.*))) KEEP(*(.init_array*)) PROVIDE_HIDDEN(__init_array_end = .); }} >FLASH
    .fini_array : {{ PROVIDE_HIDDEN(__fini_array_start = .); KEEP(*(SORT(.fini_array.*))) KEEP(*(.fini_array*)) PROVIDE_HIDDEN(__fini_array_end = .); }} >FLASH
    _sidata = LOADADDR(.data);
    .data :
    {{
        . = ALIGN(4);
        _sdata = .;
        *(.data) *(.data*)
        . = ALIGN(4);
        _edata = .;
    }} >RAM AT> FLASH
    .bss :
    {{
        . = ALIGN(4);
        _sbss = .;
        __bss_start__ = _sbss;
        *(.bss) *(.bss*) *(COMMON)
        . = ALIGN(4);
        _ebss = .;
        __bss_end__ = _ebss;
    }} >RAM
    ._user_heap_stack :
    {{
        . = ALIGN(8);
        PROVIDE(end = .);
        PROVIDE(_end = .);
        . = . + _Min_Heap_Size;
        . = . + _Min_Stack_Size;
        . = ALIGN(8);
    }} >RAM
    .ARM.attributes 0 : {{ *(.ARM.attributes) }}
}}
"""
