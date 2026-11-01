"""Component drivers (device-independent C written against the generated HAL).

DRIVERS[name] = (needs, header_body, source_body); needs = HAL modules the driver uses.
"""

DRIVERS: dict[str, tuple[tuple, str, str]] = {}


def d(name, needs, h, c):
    DRIVERS[name] = (tuple(needs), h, c)


# --------------------------------------------------------------------------- simple outputs
for _n, _what, _extra_h, _extra_c in (
    ("led", "LED", "", ""),
    ("buzzer", "active buzzer", "/** @brief Sound for ms milliseconds (blocking). */\nvoid buzzer_beep(const buzzer_t *dev, uint16_t ms);\n",
     "void buzzer_beep(const buzzer_t *dev, uint16_t ms)\n{\n    buzzer_on(dev);\n    hal_delay_ms(ms);\n    buzzer_off(dev);\n}\n"),
    ("relay", "relay / relay module", "", ""),
    ("motor", "DC motor through a low-side switch", "", ""),
):
    _t = f"{_n}_t"
    d(_n, ("gpio", "timer"),
      f"""
#include "hal_gpio.h"
#include "hal_timer.h"

/** @brief {_what} wired to one GPIO. */
typedef struct
{{
    hal_gpio_t pin;          /**< control pin */
    bool       active_high;  /**< true: pin high = on */
}} {_t};

/** @brief Switch on. */
void {_n}_on(const {_t} *dev);
/** @brief Switch off. */
void {_n}_off(const {_t} *dev);
/** @brief Invert the state. */
void {_n}_toggle(const {_t} *dev);
/** @brief Set the state. @param on true = on */
void {_n}_set(const {_t} *dev, bool on);
{_extra_h}""",
      f"""
#include "{_n}.h"

void {_n}_set(const {_t} *dev, bool on)
{{
    if (dev != NULL)
    {{
        hal_gpio_write(&dev->pin, on == dev->active_high);
    }}
}}

void {_n}_on(const {_t} *dev)     {{ {_n}_set(dev, true); }}
void {_n}_off(const {_t} *dev)    {{ {_n}_set(dev, false); }}
void {_n}_toggle(const {_t} *dev) {{ if (dev != NULL) {{ hal_gpio_toggle(&dev->pin); }} }}
{_extra_c}""")

d("rgb", ("gpio",), """
#include "hal_gpio.h"

#define RGB_RED     (1U << 0)   /**< colour bit: red   */
#define RGB_GREEN   (1U << 1)   /**< colour bit: green */
#define RGB_BLUE    (1U << 2)   /**< colour bit: blue  */

/** @brief RGB LED, one GPIO per colour. */
typedef struct
{
    hal_gpio_t r, g, b;      /**< colour pins */
    bool       active_high;  /**< common cathode = true */
} rgb_t;

/** @brief Show a colour made of RGB_RED | RGB_GREEN | RGB_BLUE. */
void rgb_set(const rgb_t *dev, uint8_t colour);
""", """
#include "rgb.h"

void rgb_set(const rgb_t *dev, uint8_t colour)
{
    if (dev != NULL)
    {
        hal_gpio_write(&dev->r, ((colour & RGB_RED) != 0U) == dev->active_high);
        hal_gpio_write(&dev->g, ((colour & RGB_GREEN) != 0U) == dev->active_high);
        hal_gpio_write(&dev->b, ((colour & RGB_BLUE) != 0U) == dev->active_high);
    }
}
""")

d("ledbar", ("gpio",), """
#include "hal_gpio.h"

/** @brief LED bar graph: one GPIO per segment. */
typedef struct
{
    const hal_gpio_t *pins;  /**< segment pins, bottom first */
    uint8_t           count; /**< number of segments */
} ledbar_t;

/** @brief Light the first `level` segments. */
void ledbar_level(const ledbar_t *dev, uint8_t level);
""", """
#include "ledbar.h"

void ledbar_level(const ledbar_t *dev, uint8_t level)
{
    uint8_t i;
    if (dev != NULL)
    {
        for (i = 0U; i < dev->count; i++)
        {
            hal_gpio_write(&dev->pins[i], i < level);
        }
    }
}
""")

_SEG = "static const uint8_t SEG7_FONT[10] = {0x3FU, 0x06U, 0x5BU, 0x4FU, 0x66U, 0x6DU, 0x7DU, 0x07U, 0x7FU, 0x6FU};"
d("seg7", ("gpio",), """
#include "hal_gpio.h"

#define SEG7_SEGMENTS   (8U)   /**< a..g + dp */

/** @brief Single 7-segment digit, one GPIO per segment (a..g, dp). */
typedef struct
{
    const hal_gpio_t *seg;          /**< SEG7_SEGMENTS pins */
    bool              common_anode; /**< true: segment on = pin low */
} seg7_t;

/** @brief Show 0..9 (other values blank the display). */
hal_status_t seg7_show(const seg7_t *dev, uint8_t digit);
""", f"""
#include "seg7.h"

{_SEG}

hal_status_t seg7_show(const seg7_t *dev, uint8_t digit)
{{
    uint8_t i;
    uint8_t bits;
    if (dev == NULL) {{ return HAL_ERR_PARAM; }}
    bits = (digit < 10U) ? SEG7_FONT[digit] : 0U;
    for (i = 0U; i < SEG7_SEGMENTS; i++)
    {{
        hal_gpio_write(&dev->seg[i], (((bits >> i) & 1U) != 0U) != dev->common_anode);
    }}
    return HAL_OK;
}}
""")

d("hc595", ("gpio", "spi"), """
#include "hal_gpio.h"
#include "hal_spi.h"

/** @brief 74HC595 shift register on SPI (SER = SDO, SRCLK = SCK) with a GPIO latch (RCLK). */
typedef struct
{
    hal_spi_id_t spi;    /**< SPI bus */
    hal_gpio_t   latch;  /**< RCLK */
} hc595_t;

/** @brief Shift out one byte and latch it. */
void hc595_write(const hc595_t *dev, uint8_t value);
/** @brief Show 0..9 on a common-cathode digit wired Q0=a ... Q7=dp. */
void hc595_show_digit(const hc595_t *dev, uint8_t digit);
""", f"""
#include "hc595.h"

{_SEG}

void hc595_write(const hc595_t *dev, uint8_t value)
{{
    if (dev != NULL)
    {{
        hal_gpio_write(&dev->latch, false);
        (void)hal_spi_transfer(dev->spi, value);
        hal_gpio_write(&dev->latch, true);
    }}
}}

void hc595_show_digit(const hc595_t *dev, uint8_t digit)
{{
    hc595_write(dev, (digit < 10U) ? SEG7_FONT[digit] : 0U);
}}
""")

d("lcd_hd44780", ("gpio", "timer"), """
#include "hal_gpio.h"
#include "hal_timer.h"

/** @brief HD44780 character LCD in 4-bit mode, R/W tied to GND. */
typedef struct
{
    hal_gpio_t rs, en;          /**< register select, enable */
    hal_gpio_t d4, d5, d6, d7;  /**< data nibble */
    uint8_t    cols, rows;      /**< geometry */
} lcd_t;

/** @brief Power-on initialisation (datasheet 4-bit sequence). */
void lcd_init(const lcd_t *dev);
/** @brief Clear and home. */
void lcd_clear(const lcd_t *dev);
/** @brief Move the cursor. */
hal_status_t lcd_goto(const lcd_t *dev, uint8_t col, uint8_t row);
/** @brief Print a string at the cursor. */
void lcd_print(const lcd_t *dev, const char *str);
/** @brief Print a signed integer. */
void lcd_print_int(const lcd_t *dev, int32_t value);
""", """
#include "lcd_hd44780.h"

#define LCD_CMD_CLEAR       (0x01U)
#define LCD_CMD_ENTRY_INC   (0x06U)
#define LCD_CMD_DISPLAY_ON  (0x0CU)
#define LCD_CMD_FUNC_4BIT2L (0x28U)
#define LCD_CMD_DDRAM       (0x80U)
#define LCD_T_CLEAR_MS      (2U)
#define LCD_T_POWERUP_MS    (40U)
#define LCD_T_CMD_US        (50U)

static void lcd_nibble(const lcd_t *dev, uint8_t n)
{
    hal_gpio_write(&dev->d4, (n & 0x01U) != 0U);
    hal_gpio_write(&dev->d5, (n & 0x02U) != 0U);
    hal_gpio_write(&dev->d6, (n & 0x04U) != 0U);
    hal_gpio_write(&dev->d7, (n & 0x08U) != 0U);
    hal_gpio_write(&dev->en, true);
    hal_delay_us(1U);
    hal_gpio_write(&dev->en, false);
    hal_delay_us(LCD_T_CMD_US);
}

static void lcd_byte(const lcd_t *dev, uint8_t b, bool data)
{
    hal_gpio_write(&dev->rs, data);
    lcd_nibble(dev, (uint8_t)(b >> 4));
    lcd_nibble(dev, (uint8_t)(b & 0x0FU));
}

void lcd_init(const lcd_t *dev)
{
    if (dev == NULL) { return; }
    hal_delay_ms(LCD_T_POWERUP_MS);
    hal_gpio_write(&dev->rs, false);
    lcd_nibble(dev, 0x03U);
    hal_delay_ms(5U);
    lcd_nibble(dev, 0x03U);
    lcd_nibble(dev, 0x03U);
    lcd_nibble(dev, 0x02U);
    lcd_byte(dev, LCD_CMD_FUNC_4BIT2L, false);
    lcd_byte(dev, LCD_CMD_DISPLAY_ON, false);
    lcd_byte(dev, LCD_CMD_ENTRY_INC, false);
    lcd_clear(dev);
}

void lcd_clear(const lcd_t *dev)
{
    if (dev != NULL)
    {
        lcd_byte(dev, LCD_CMD_CLEAR, false);
        hal_delay_ms(LCD_T_CLEAR_MS);
    }
}

hal_status_t lcd_goto(const lcd_t *dev, uint8_t col, uint8_t row)
{
    static const uint8_t row_addr[4] = {0x00U, 0x40U, 0x14U, 0x54U};
    if ((dev == NULL) || (col >= dev->cols) || (row >= dev->rows) || (row > 3U)) { return HAL_ERR_PARAM; }
    lcd_byte(dev, (uint8_t)(LCD_CMD_DDRAM | (uint8_t)(row_addr[row] + col)), false);
    return HAL_OK;
}

void lcd_print(const lcd_t *dev, const char *str)
{
    if ((dev == NULL) || (str == NULL)) { return; }
    while (*str != '\\0')
    {
        lcd_byte(dev, (uint8_t)*str, true);
        str++;
    }
}

void lcd_print_int(const lcd_t *dev, int32_t value)
{
    char buf[12];
    uint8_t i = 0U;
    uint32_t u = (value < 0) ? (uint32_t)(-value) : (uint32_t)value;
    if (value < 0) { lcd_byte(dev, (uint8_t)'-', true); }
    do { buf[i] = (char)('0' + (char)(u % 10U)); i++; u /= 10U; } while ((u != 0U) && (i < sizeof(buf)));
    while (i > 0U) { i--; lcd_byte(dev, (uint8_t)buf[i], true); }
}
""")

d("ssd1306", ("i2c",), """
#include "hal_i2c.h"

/** @brief SSD1306 128x64 OLED on I2C. */
typedef struct
{
    hal_i2c_id_t i2c;   /**< bus */
    uint8_t      addr;  /**< 7-bit address (0x3C / 0x3D) */
} ssd1306_t;

/** @brief Initialise the panel (charge pump on, horizontal addressing). */
hal_status_t ssd1306_init(const ssd1306_t *dev);
/** @brief Clear the whole display RAM. */
hal_status_t ssd1306_clear(const ssd1306_t *dev);
/** @brief Print an integer with a 5x7 digit font at text cell (col 0..20, page 0..7). */
hal_status_t ssd1306_print_int(const ssd1306_t *dev, uint8_t col, uint8_t page, int32_t value);
""", """
#include "ssd1306.h"

#define SSD1306_CTRL_CMD    (0x00U)
#define SSD1306_CTRL_DATA   (0x40U)
#define SSD1306_PAGES       (8U)
#define SSD1306_WIDTH       (128U)
#define SSD1306_CHAR_W      (6U)

static const uint8_t FONT_DIGITS[13][5] = {
    {0x3EU, 0x51U, 0x49U, 0x45U, 0x3EU}, {0x00U, 0x42U, 0x7FU, 0x40U, 0x00U}, {0x42U, 0x61U, 0x51U, 0x49U, 0x46U},
    {0x21U, 0x41U, 0x45U, 0x4BU, 0x31U}, {0x18U, 0x14U, 0x12U, 0x7FU, 0x10U}, {0x27U, 0x45U, 0x45U, 0x45U, 0x39U},
    {0x3CU, 0x4AU, 0x49U, 0x49U, 0x30U}, {0x01U, 0x71U, 0x09U, 0x05U, 0x03U}, {0x36U, 0x49U, 0x49U, 0x49U, 0x36U},
    {0x06U, 0x49U, 0x49U, 0x29U, 0x1EU}, {0x08U, 0x08U, 0x08U, 0x08U, 0x08U}, {0x00U, 0x60U, 0x60U, 0x00U, 0x00U},
    {0x00U, 0x00U, 0x00U, 0x00U, 0x00U}};
#define FONT_MINUS  (10U)
#define FONT_SPACE  (12U)

static hal_status_t cmd(const ssd1306_t *dev, uint8_t c)
{
    uint8_t buf[2];
    buf[0] = SSD1306_CTRL_CMD;
    buf[1] = c;
    return hal_i2c_write(dev->i2c, dev->addr, buf, sizeof(buf));
}

static hal_status_t goto_cell(const ssd1306_t *dev, uint8_t x, uint8_t page)
{
    hal_status_t st = cmd(dev, (uint8_t)(0xB0U | (page & 0x07U)));
    if (st == HAL_OK) { st = cmd(dev, (uint8_t)(x & 0x0FU)); }
    if (st == HAL_OK) { st = cmd(dev, (uint8_t)(0x10U | (x >> 4))); }
    return st;
}

hal_status_t ssd1306_init(const ssd1306_t *dev)
{
    static const uint8_t init_seq[] = {0xAEU, 0xD5U, 0x80U, 0xA8U, 0x3FU, 0xD3U, 0x00U, 0x40U, 0x8DU, 0x14U,
                                       0x20U, 0x02U, 0xA1U, 0xC8U, 0xDAU, 0x12U, 0x81U, 0x7FU, 0xD9U, 0xF1U,
                                       0xDBU, 0x40U, 0xA4U, 0xA6U, 0xAFU};
    uint8_t i;
    hal_status_t st = HAL_OK;
    if (dev == NULL) { return HAL_ERR_PARAM; }
    for (i = 0U; (i < sizeof(init_seq)) && (st == HAL_OK); i++) { st = cmd(dev, init_seq[i]); }
    return (st == HAL_OK) ? ssd1306_clear(dev) : st;
}

hal_status_t ssd1306_clear(const ssd1306_t *dev)
{
    uint8_t buf[17];
    uint8_t page, chunk;
    hal_status_t st = HAL_OK;
    if (dev == NULL) { return HAL_ERR_PARAM; }
    for (chunk = 0U; chunk < sizeof(buf); chunk++) { buf[chunk] = 0U; }
    buf[0] = SSD1306_CTRL_DATA;
    for (page = 0U; (page < SSD1306_PAGES) && (st == HAL_OK); page++)
    {
        st = goto_cell(dev, 0U, page);
        for (chunk = 0U; (chunk < (SSD1306_WIDTH / 16U)) && (st == HAL_OK); chunk++)
        {
            st = hal_i2c_write(dev->i2c, dev->addr, buf, sizeof(buf));
        }
    }
    return st;
}

hal_status_t ssd1306_print_int(const ssd1306_t *dev, uint8_t col, uint8_t page, int32_t value)
{
    char digits[12];
    uint8_t n = 0U;
    uint8_t glyph[SSD1306_CHAR_W + 1U];
    uint8_t k;
    uint32_t u = (value < 0) ? (uint32_t)(-value) : (uint32_t)value;
    hal_status_t st;
    if (dev == NULL) { return HAL_ERR_PARAM; }
    do { digits[n] = (char)(u % 10U); n++; u /= 10U; } while ((u != 0U) && (n < 11U));
    if (value < 0) { digits[n] = (char)FONT_MINUS; n++; }
    st = goto_cell(dev, (uint8_t)(col * SSD1306_CHAR_W), page);
    glyph[0] = SSD1306_CTRL_DATA;
    while ((n > 0U) && (st == HAL_OK))
    {
        n--;
        for (k = 0U; k < 5U; k++) { glyph[k + 1U] = FONT_DIGITS[(uint8_t)digits[n]][k]; }
        glyph[SSD1306_CHAR_W] = 0U;
        st = hal_i2c_write(dev->i2c, dev->addr, glyph, sizeof(glyph));
    }
    return st;
}
""")

d("tone", ("pwm",), """
#include "hal_pwm.h"

/** @brief Passive buzzer on a PWM channel (frequency fixed at generation time). */
typedef struct
{
    hal_pwm_id_t ch;   /**< PWM channel */
} tone_t;

/** @brief Start the tone (50 % duty). */
hal_status_t tone_on(const tone_t *dev);
/** @brief Silence (0 % duty). */
hal_status_t tone_off(const tone_t *dev);
""", """
#include "tone.h"

#define TONE_DUTY_ON_PERMILLE   (500U)

hal_status_t tone_on(const tone_t *dev)  { return (dev == NULL) ? HAL_ERR_PARAM : hal_pwm_set_permille(dev->ch, TONE_DUTY_ON_PERMILLE); }
hal_status_t tone_off(const tone_t *dev) { return (dev == NULL) ? HAL_ERR_PARAM : hal_pwm_set_permille(dev->ch, 0U); }
""")

d("servo", ("pwm",), """
#include "hal_pwm.h"

#define SERVO_MAX_DEG   (180U)   /**< full travel */

/** @brief Hobby servo on a 50 Hz PWM channel. */
typedef struct
{
    hal_pwm_id_t ch;      /**< PWM channel */
    uint16_t     min_us;  /**< pulse at 0 degrees */
    uint16_t     max_us;  /**< pulse at 180 degrees */
} servo_t;

/** @brief Move to an angle. @return HAL_ERR_PARAM when deg > 180 */
hal_status_t servo_set(const servo_t *dev, uint16_t deg);
""", """
#include "servo.h"

hal_status_t servo_set(const servo_t *dev, uint16_t deg)
{
    uint32_t us;
    if ((dev == NULL) || (deg > SERVO_MAX_DEG) || (dev->max_us < dev->min_us)) { return HAL_ERR_PARAM; }
    us = (uint32_t)dev->min_us + (((uint32_t)(dev->max_us - dev->min_us) * deg) / SERVO_MAX_DEG);
    return hal_pwm_set_us(dev->ch, (uint16_t)us);
}
""")

d("hbridge", ("gpio", "pwm"), """
#include "hal_gpio.h"
#include "hal_pwm.h"

#define HBRIDGE_FULL_SPEED  (1000)   /**< per-mille */

/** @brief One H-bridge channel (L298N / DRV8833): direction pins + PWM enable. */
typedef struct
{
    hal_gpio_t   in1, in2;  /**< direction */
    hal_pwm_id_t en;        /**< speed */
} hbridge_t;

/** @brief Drive the motor. @param speed -1000 (full reverse) .. 0 (brake) .. 1000 (full forward) */
hal_status_t hbridge_drive(const hbridge_t *dev, int16_t speed);
""", """
#include "hbridge.h"

hal_status_t hbridge_drive(const hbridge_t *dev, int16_t speed)
{
    uint16_t mag;
    if ((dev == NULL) || (speed > HBRIDGE_FULL_SPEED) || (speed < -HBRIDGE_FULL_SPEED)) { return HAL_ERR_PARAM; }
    mag = (uint16_t)((speed < 0) ? -speed : speed);
    hal_gpio_write(&dev->in1, speed > 0);
    hal_gpio_write(&dev->in2, speed < 0);
    return hal_pwm_set_permille(dev->en, mag);
}
""")

d("stepper_uln", ("gpio", "timer"), """
#include "hal_gpio.h"
#include "hal_timer.h"

/** @brief Unipolar stepper (28BYJ-48) through a ULN2003, half-step sequence. */
typedef struct
{
    hal_gpio_t in[4];     /**< IN1..IN4 */
    uint8_t   *phase;     /**< RAM: current half-step 0..7 */
} stepper_uln_t;

/** @brief Move a number of half-steps (negative = reverse). */
void stepper_uln_move(const stepper_uln_t *dev, int32_t steps, uint16_t ms_per_step);
""", """
#include "stepper_uln.h"

static const uint8_t HALF_STEP[8] = {0x01U, 0x03U, 0x02U, 0x06U, 0x04U, 0x0CU, 0x08U, 0x09U};
#define STEPPER_PHASES  (8U)

void stepper_uln_move(const stepper_uln_t *dev, int32_t steps, uint16_t ms_per_step)
{
    uint8_t k;
    if ((dev == NULL) || (dev->phase == NULL)) { return; }
    while (steps != 0)
    {
        if (steps > 0) { *dev->phase = (uint8_t)((*dev->phase + 1U) % STEPPER_PHASES); steps--; }
        else           { *dev->phase = (uint8_t)((*dev->phase + STEPPER_PHASES - 1U) % STEPPER_PHASES); steps++; }
        for (k = 0U; k < 4U; k++) { hal_gpio_write(&dev->in[k], ((HALF_STEP[*dev->phase] >> k) & 1U) != 0U); }
        hal_delay_ms(ms_per_step);
    }
    for (k = 0U; k < 4U; k++) { hal_gpio_write(&dev->in[k], false); }
}
""")

d("stepper_a4988", ("gpio", "timer"), """
#include "hal_gpio.h"
#include "hal_timer.h"

/** @brief Bipolar stepper through an A4988 / DRV8825 (STEP/DIR/EN, EN active low). */
typedef struct
{
    hal_gpio_t step, dir, en;   /**< driver pins */
} a4988_t;

/** @brief Move a number of steps (negative = reverse) with a fixed step period. */
void a4988_move(const a4988_t *dev, int32_t steps, uint16_t us_per_step);
""", """
#include "stepper_a4988.h"

#define A4988_PULSE_US  (2U)   /* >= 1 us high time */

void a4988_move(const a4988_t *dev, int32_t steps, uint16_t us_per_step)
{
    if (dev == NULL) { return; }
    hal_gpio_write(&dev->dir, steps >= 0);
    hal_gpio_write(&dev->en, false);
    while (steps != 0)
    {
        hal_gpio_write(&dev->step, true);
        hal_delay_us(A4988_PULSE_US);
        hal_gpio_write(&dev->step, false);
        hal_delay_us((us_per_step > A4988_PULSE_US) ? (uint16_t)(us_per_step - A4988_PULSE_US) : 0U);
        steps += (steps > 0) ? -1 : 1;
    }
    hal_gpio_write(&dev->en, true);
}
""")

# --------------------------------------------------------------------------- inputs
d("button", ("gpio", "timer"), """
#include "hal_gpio.h"
#include "hal_timer.h"

/** @brief Debounce state kept in RAM. */
typedef struct
{
    bool     stable;      /**< debounced level (true = pressed) */
    bool     last_raw;    /**< last sampled level */
    uint32_t changed_ms;  /**< time of last raw change */
} button_state_t;

/** @brief Push button / switch on a GPIO. */
typedef struct
{
    hal_gpio_t      pin;          /**< input pin */
    bool            active_high;  /**< false: pressed = low (pull-up + switch to GND) */
    uint16_t        debounce_ms;  /**< stable time before a change is accepted */
    button_state_t *state;        /**< RAM state */
} button_t;

/** @brief Debounced level. @return true while pressed */
bool button_read(const button_t *dev);
/** @brief True once per press (rising edge of the debounced level). */
bool button_pressed(const button_t *dev);
""", """
#include "button.h"

static bool raw_pressed(const button_t *dev)
{
    return hal_gpio_read(&dev->pin) == dev->active_high;
}

bool button_read(const button_t *dev)
{
    bool raw;
    uint32_t now;
    if ((dev == NULL) || (dev->state == NULL)) { return false; }
    raw = raw_pressed(dev);
    now = hal_millis();
    if (raw != dev->state->last_raw)
    {
        dev->state->last_raw = raw;
        dev->state->changed_ms = now;
    }
    else if ((now - dev->state->changed_ms) >= dev->debounce_ms)
    {
        dev->state->stable = raw;
    }
    else
    {
        /* still bouncing */
    }
    return dev->state->stable;
}

bool button_pressed(const button_t *dev)
{
    bool before;
    if ((dev == NULL) || (dev->state == NULL)) { return false; }
    before = dev->state->stable;
    return (button_read(dev) && !before);
}
""")

d("keypad", ("gpio", "timer"), """
#include "hal_gpio.h"
#include "hal_timer.h"

#define KEYPAD_SIZE (4U)   /**< 4 x 4 matrix */

/** @brief 4x4 matrix keypad: rows driven low one at a time, columns read with pull-ups. */
typedef struct
{
    hal_gpio_t  rows[KEYPAD_SIZE];  /**< outputs */
    hal_gpio_t  cols[KEYPAD_SIZE];  /**< inputs, pulled up */
    const char *keys;               /**< 16 key labels, row-major */
} keypad_t;

/** @brief Scan once. @return key label, or '\\0' when no key is down */
char keypad_scan(const keypad_t *dev);
""", """
#include "keypad.h"

#define KEYPAD_SETTLE_US    (5U)

char keypad_scan(const keypad_t *dev)
{
    uint8_t r, c;
    char key = '\\0';
    if (dev == NULL) { return key; }
    for (r = 0U; r < KEYPAD_SIZE; r++) { hal_gpio_write(&dev->rows[r], true); }
    for (r = 0U; (r < KEYPAD_SIZE) && (key == '\\0'); r++)
    {
        hal_gpio_write(&dev->rows[r], false);
        hal_delay_us(KEYPAD_SETTLE_US);
        for (c = 0U; c < KEYPAD_SIZE; c++)
        {
            if (!hal_gpio_read(&dev->cols[c])) { key = dev->keys[(r * KEYPAD_SIZE) + c]; }
        }
        hal_gpio_write(&dev->rows[r], true);
    }
    return key;
}
""")

d("encoder", ("gpio",), """
#include "hal_gpio.h"

/** @brief Quadrature rotary encoder (A/B to GND with pull-ups) + push switch. */
typedef struct
{
    hal_gpio_t a, b, sw;   /**< inputs */
    uint8_t   *last;       /**< RAM: previous AB state */
    int32_t   *count;      /**< RAM: position */
} encoder_t;

/** @brief Poll often (>= 1 kHz). @return current position */
int32_t encoder_poll(const encoder_t *dev);
""", """
#include "encoder.h"

static const int8_t QDEC[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};

int32_t encoder_poll(const encoder_t *dev)
{
    uint8_t now;
    if ((dev == NULL) || (dev->last == NULL) || (dev->count == NULL)) { return 0; }
    now = (uint8_t)((hal_gpio_read(&dev->a) ? 2U : 0U) | (hal_gpio_read(&dev->b) ? 1U : 0U));
    *dev->count += QDEC[(uint8_t)((*dev->last << 2) | now)];
    *dev->last = now;
    return *dev->count;
}
""")

_AN_H = """
#include "hal_adc.h"

/** @brief {what} on one ADC channel. */
typedef struct
{{
    uint8_t channel;   /**< ANx */
{extra}}} {t};
"""
d("pot", ("adc",), _AN_H.format(what="Potentiometer wiper", t="pot_t", extra="") + """
/** @brief Raw 10-bit reading. */
hal_status_t pot_read_raw(const pot_t *dev, uint16_t *out);
/** @brief Position in per-mille (0..1000). */
hal_status_t pot_read_permille(const pot_t *dev, uint16_t *out);
""", """
#include "pot.h"

hal_status_t pot_read_raw(const pot_t *dev, uint16_t *out)
{
    return ((dev == NULL) || (out == NULL)) ? HAL_ERR_PARAM : hal_adc_read(dev->channel, out);
}

hal_status_t pot_read_permille(const pot_t *dev, uint16_t *out)
{
    uint16_t raw = 0U;
    hal_status_t st = pot_read_raw(dev, &raw);
    if (st == HAL_OK) { *out = (uint16_t)(((uint32_t)raw * 1000UL) / HAL_ADC_MAX); }
    return st;
}
""")
d("analog_sensor", ("adc",), _AN_H.format(what="Resistive divider sensor (LDR)", t="analog_sensor_t", extra="") + """
/** @brief Level in per-mille of full scale. */
hal_status_t analog_sensor_permille(const analog_sensor_t *dev, uint16_t *out);
""", """
#include "analog_sensor.h"

hal_status_t analog_sensor_permille(const analog_sensor_t *dev, uint16_t *out)
{
    uint16_t raw = 0U;
    hal_status_t st;
    if ((dev == NULL) || (out == NULL)) { return HAL_ERR_PARAM; }
    st = hal_adc_read(dev->channel, &raw);
    if (st == HAL_OK) { *out = (uint16_t)(((uint32_t)raw * 1000UL) / HAL_ADC_MAX); }
    return st;
}
""")
d("joystick", ("adc", "gpio"), """
#include "hal_adc.h"
#include "hal_gpio.h"

/** @brief Two-axis joystick + push switch (to GND). */
typedef struct
{
    uint8_t    ch_x, ch_y;  /**< ANx channels */
    hal_gpio_t sw;          /**< switch input, pulled up */
} joystick_t;

/** @brief Read both axes, centred: -512..511. */
hal_status_t joystick_read(const joystick_t *dev, int16_t *x, int16_t *y);
/** @brief True while the stick is pressed. */
bool joystick_pressed(const joystick_t *dev);
""", """
#include "joystick.h"

#define JOY_CENTRE  (512)

hal_status_t joystick_read(const joystick_t *dev, int16_t *x, int16_t *y)
{
    uint16_t rx = 0U, ry = 0U;
    hal_status_t st;
    if ((dev == NULL) || (x == NULL) || (y == NULL)) { return HAL_ERR_PARAM; }
    st = hal_adc_read(dev->ch_x, &rx);
    if (st == HAL_OK) { st = hal_adc_read(dev->ch_y, &ry); }
    *x = (int16_t)((int16_t)rx - JOY_CENTRE);
    *y = (int16_t)((int16_t)ry - JOY_CENTRE);
    return st;
}

bool joystick_pressed(const joystick_t *dev)
{
    return (dev != NULL) && !hal_gpio_read(&dev->sw);
}
""")
d("ntc", ("adc",), _AN_H.format(what="NTC thermistor (to GND) under a fixed resistor (to VDD)", t="ntc_t",
      extra="    uint32_t r_fixed;  /**< top resistor, ohms */\n    uint32_t r25;      /**< NTC at 25 C, ohms */\n    uint16_t beta;     /**< B constant, K */\n") + """
/** @brief Temperature in 0.1 degC (beta equation). */
hal_status_t ntc_read_c_x10(const ntc_t *dev, int16_t *out);
""", """
#include <math.h>
#include "ntc.h"

#define KELVIN_25C  (298.15f)
#define KELVIN_0C   (273.15f)

hal_status_t ntc_read_c_x10(const ntc_t *dev, int16_t *out)
{
    uint16_t raw = 0U;
    float r, inv_t;
    hal_status_t st;
    if ((dev == NULL) || (out == NULL)) { return HAL_ERR_PARAM; }
    st = hal_adc_read(dev->channel, &raw);
    if (st != HAL_OK) { return st; }
    if ((raw == 0U) || (raw >= HAL_ADC_MAX)) { return HAL_ERR_DEVICE; }
    r = ((float)dev->r_fixed * (float)raw) / (float)(HAL_ADC_MAX - raw);
    inv_t = (1.0f / KELVIN_25C) + (logf(r / (float)dev->r25) / (float)dev->beta);
    *out = (int16_t)(((1.0f / inv_t) - KELVIN_0C) * 10.0f);
    return HAL_OK;
}
""")
d("lm35", ("adc",), _AN_H.format(what="LM35 (10 mV/degC)", t="lm35_t", extra="") + """
/** @brief Temperature in 0.1 degC. */
hal_status_t lm35_read_c_x10(const lm35_t *dev, int16_t *out);
""", """
#include "lm35.h"

hal_status_t lm35_read_c_x10(const lm35_t *dev, int16_t *out)
{
    uint16_t raw = 0U;
    hal_status_t st;
    if ((dev == NULL) || (out == NULL)) { return HAL_ERR_PARAM; }
    st = hal_adc_read(dev->channel, &raw);
    /* 1 mV = 0.1 degC */
    if (st == HAL_OK) { *out = (int16_t)(((uint32_t)raw * HAL_ADC_VREF_MV) / HAL_ADC_MAX); }
    return st;
}
""")

# --------------------------------------------------------------------------- timed single-wire
d("dht22", ("gpio", "timer"), """
#include "hal_gpio.h"
#include "hal_timer.h"

/** @brief DHT22 / AM2302 on one open-drain GPIO (10 k pull-up). */
typedef struct
{
    hal_gpio_t pin;   /**< DATA */
} dht22_t;

/** @brief Read temperature (0.1 degC) and humidity (0.1 %RH). Allow >= 2 s between reads. */
hal_status_t dht22_read(const dht22_t *dev, int16_t *t_x10, uint16_t *rh_x10);
""", """
#include "dht22.h"

#define DHT_START_MS     (2U)
#define DHT_SAMPLE_US    (35U)
#define DHT_TIMEOUT      (200U)   /* polls of ~1 us */
#define DHT_BITS         (40U)

static hal_status_t wait_level(const dht22_t *dev, bool level)
{
    uint16_t n = 0U;
    while (hal_gpio_read(&dev->pin) != level)
    {
        if (++n > DHT_TIMEOUT) { return HAL_ERR_TIMEOUT; }
        hal_delay_us(1U);
    }
    return HAL_OK;
}

hal_status_t dht22_read(const dht22_t *dev, int16_t *t_x10, uint16_t *rh_x10)
{
    uint8_t data[5] = {0U, 0U, 0U, 0U, 0U};
    uint8_t i;
    hal_status_t st = HAL_OK;
    if ((dev == NULL) || (t_x10 == NULL) || (rh_x10 == NULL)) { return HAL_ERR_PARAM; }
    hal_gpio_write(&dev->pin, false);
    hal_gpio_set_output(&dev->pin, true);
    hal_delay_ms(DHT_START_MS);
    hal_gpio_set_output(&dev->pin, false);          /* release: pull-up takes the line high */
    st = wait_level(dev, false);
    if (st == HAL_OK) { st = wait_level(dev, true); }
    if (st == HAL_OK) { st = wait_level(dev, false); }
    for (i = 0U; (i < DHT_BITS) && (st == HAL_OK); i++)
    {
        st = wait_level(dev, true);
        hal_delay_us(DHT_SAMPLE_US);
        data[i / 8U] = (uint8_t)((data[i / 8U] << 1) | (hal_gpio_read(&dev->pin) ? 1U : 0U));
        if (st == HAL_OK) { st = wait_level(dev, false); }
    }
    if (st != HAL_OK) { return st; }
    if ((uint8_t)(data[0] + data[1] + data[2] + data[3]) != data[4]) { return HAL_ERR_DEVICE; }
    *rh_x10 = (uint16_t)(((uint16_t)data[0] << 8) | data[1]);
    *t_x10 = (int16_t)((((uint16_t)data[2] & 0x7FU) << 8) | data[3]);
    if ((data[2] & 0x80U) != 0U) { *t_x10 = (int16_t)(-*t_x10); }
    return HAL_OK;
}
""")

d("ds18b20", ("gpio", "timer"), """
#include "hal_gpio.h"
#include "hal_timer.h"

/** @brief DS18B20 on a 1-Wire bus (4.7 k pull-up), single device (SKIP ROM). */
typedef struct
{
    hal_gpio_t pin;   /**< DQ */
} ds18b20_t;

/** @brief Start a conversion, wait 750 ms, read. @param out temperature in 1/16 degC */
hal_status_t ds18b20_read(const ds18b20_t *dev, int16_t *out);
""", """
#include "ds18b20.h"

#define OW_RESET_US      (480U)
#define OW_PRESENCE_US   (70U)
#define OW_SLOT_US       (60U)
#define OW_CONVERT_MS    (750U)
#define DS_SKIP_ROM      (0xCCU)
#define DS_CONVERT_T     (0x44U)
#define DS_READ_SCRATCH  (0xBEU)

static void ow_low(const ds18b20_t *d)     { hal_gpio_write(&d->pin, false); hal_gpio_set_output(&d->pin, true); }
static void ow_release(const ds18b20_t *d) { hal_gpio_set_output(&d->pin, false); }

static bool ow_reset(const ds18b20_t *d)
{
    bool present;
    ow_low(d);
    hal_delay_us(OW_RESET_US);
    ow_release(d);
    hal_delay_us(OW_PRESENCE_US);
    present = !hal_gpio_read(&d->pin);
    hal_delay_us(OW_RESET_US - OW_PRESENCE_US);
    return present;
}

static void ow_write(const ds18b20_t *d, uint8_t b)
{
    uint8_t i;
    for (i = 0U; i < 8U; i++)
    {
        ow_low(d);
        hal_delay_us(((b >> i) & 1U) != 0U ? 2U : OW_SLOT_US);
        ow_release(d);
        hal_delay_us(((b >> i) & 1U) != 0U ? OW_SLOT_US : 2U);
    }
}

static uint8_t ow_read(const ds18b20_t *d)
{
    uint8_t i, b = 0U;
    for (i = 0U; i < 8U; i++)
    {
        ow_low(d);
        hal_delay_us(2U);
        ow_release(d);
        hal_delay_us(10U);
        if (hal_gpio_read(&d->pin)) { b |= (uint8_t)(1U << i); }
        hal_delay_us(OW_SLOT_US - 12U);
    }
    return b;
}

hal_status_t ds18b20_read(const ds18b20_t *dev, int16_t *out)
{
    uint8_t lo, hi;
    if ((dev == NULL) || (out == NULL)) { return HAL_ERR_PARAM; }
    if (!ow_reset(dev)) { return HAL_ERR_TIMEOUT; }
    ow_write(dev, DS_SKIP_ROM);
    ow_write(dev, DS_CONVERT_T);
    hal_delay_ms(OW_CONVERT_MS);
    if (!ow_reset(dev)) { return HAL_ERR_TIMEOUT; }
    ow_write(dev, DS_SKIP_ROM);
    ow_write(dev, DS_READ_SCRATCH);
    lo = ow_read(dev);
    hi = ow_read(dev);
    *out = (int16_t)(((uint16_t)hi << 8) | lo);
    return HAL_OK;
}
""")

# --------------------------------------------------------------------------- I2C devices
_I2C_T = """
#include "hal_i2c.h"

/** @brief {what} */
typedef struct
{{
    hal_i2c_id_t i2c;   /**< bus */
    uint8_t      addr;  /**< 7-bit address */
}} {t};
"""
d("i2c_dev", ("i2c",), _I2C_T.format(what="Generic I2C register device.", t="i2c_dev_t") + """
/** @brief Write one 8-bit register. */
hal_status_t i2c_dev_write_reg(const i2c_dev_t *dev, uint8_t reg, uint8_t value);
/** @brief Read len bytes starting at reg. */
hal_status_t i2c_dev_read_regs(const i2c_dev_t *dev, uint8_t reg, uint8_t *out, size_t len);
""", """
#include "i2c_dev.h"

hal_status_t i2c_dev_write_reg(const i2c_dev_t *dev, uint8_t reg, uint8_t value)
{
    uint8_t buf[2];
    if (dev == NULL) { return HAL_ERR_PARAM; }
    buf[0] = reg;
    buf[1] = value;
    return hal_i2c_write(dev->i2c, dev->addr, buf, sizeof(buf));
}

hal_status_t i2c_dev_read_regs(const i2c_dev_t *dev, uint8_t reg, uint8_t *out, size_t len)
{
    return (dev == NULL) ? HAL_ERR_PARAM : hal_i2c_write_read(dev->i2c, dev->addr, &reg, 1U, out, len);
}
""")
d("tmp102", ("i2c",), _I2C_T.format(what="TI TMP102 temperature sensor (12-bit, 0.0625 degC/LSB).", t="tmp102_t") + """
/** @brief Temperature in 1/16 degC. */
hal_status_t tmp102_read(const tmp102_t *dev, int16_t *out);
""", """
#include "tmp102.h"

#define TMP102_REG_TEMP  (0x00U)

hal_status_t tmp102_read(const tmp102_t *dev, int16_t *out)
{
    uint8_t reg = TMP102_REG_TEMP;
    uint8_t rx[2] = {0U, 0U};
    hal_status_t st;
    if ((dev == NULL) || (out == NULL)) { return HAL_ERR_PARAM; }
    st = hal_i2c_write_read(dev->i2c, dev->addr, &reg, 1U, rx, sizeof(rx));
    if (st == HAL_OK) { *out = (int16_t)((int16_t)(((uint16_t)rx[0] << 8) | rx[1]) >> 4); }
    return st;
}
""")
d("mpu6050", ("i2c",), _I2C_T.format(what="InvenSense MPU-6050 accelerometer + gyro.", t="mpu6050_t") + """
/** @brief Raw sensor sample. */
typedef struct
{
    int16_t ax, ay, az;   /**< accel, 16384 LSB/g at +-2 g */
    int16_t temp;         /**< raw temperature */
    int16_t gx, gy, gz;   /**< gyro, 131 LSB/(deg/s) at +-250 deg/s */
} mpu6050_sample_t;

/** @brief Wake the device and check WHO_AM_I. */
hal_status_t mpu6050_init(const mpu6050_t *dev);
/** @brief Read all 7 axes. */
hal_status_t mpu6050_read(const mpu6050_t *dev, mpu6050_sample_t *out);
""", """
#include "mpu6050.h"

#define MPU_REG_PWR_MGMT_1  (0x6BU)
#define MPU_REG_WHO_AM_I    (0x75U)
#define MPU_REG_ACCEL_XOUT  (0x3BU)
#define MPU_WHO_AM_I_VALUE  (0x68U)
#define MPU_SAMPLE_BYTES    (14U)

hal_status_t mpu6050_init(const mpu6050_t *dev)
{
    uint8_t wake[2] = {MPU_REG_PWR_MGMT_1, 0x00U};
    uint8_t reg = MPU_REG_WHO_AM_I;
    uint8_t id = 0U;
    hal_status_t st;
    if (dev == NULL) { return HAL_ERR_PARAM; }
    st = hal_i2c_write(dev->i2c, dev->addr, wake, sizeof(wake));
    if (st == HAL_OK) { st = hal_i2c_write_read(dev->i2c, dev->addr, &reg, 1U, &id, 1U); }
    return ((st == HAL_OK) && ((id & 0x7EU) != MPU_WHO_AM_I_VALUE)) ? HAL_ERR_DEVICE : st;
}

hal_status_t mpu6050_read(const mpu6050_t *dev, mpu6050_sample_t *out)
{
    uint8_t reg = MPU_REG_ACCEL_XOUT;
    uint8_t b[MPU_SAMPLE_BYTES];
    int16_t v[7];
    uint8_t i;
    hal_status_t st;
    if ((dev == NULL) || (out == NULL)) { return HAL_ERR_PARAM; }
    st = hal_i2c_write_read(dev->i2c, dev->addr, &reg, 1U, b, sizeof(b));
    if (st != HAL_OK) { return st; }
    for (i = 0U; i < 7U; i++) { v[i] = (int16_t)(((uint16_t)b[2U * i] << 8) | b[(2U * i) + 1U]); }
    out->ax = v[0]; out->ay = v[1]; out->az = v[2]; out->temp = v[3];
    out->gx = v[4]; out->gy = v[5]; out->gz = v[6];
    return HAL_OK;
}
""")
d("bme280", ("i2c",), _I2C_T.format(what="Bosch BME280 temperature / pressure / humidity.", t="bme280_t") + """
/** @brief Factory calibration (read by bme280_init). */
typedef struct
{
    uint16_t t1; int16_t t2, t3;
    uint16_t p1; int16_t p2, p3, p4, p5, p6, p7, p8, p9;
    uint8_t  h1, h3; int16_t h2, h4, h5; int8_t h6;
} bme280_calib_t;

/** @brief Compensated reading. */
typedef struct
{
    int32_t  t_x100;    /**< 0.01 degC */
    uint32_t p_pa;      /**< Pa */
    uint32_t rh_x1024;  /**< %RH in Q22.10 */
} bme280_data_t;

/** @brief Check chip ID 0x60, load calibration, start normal mode (x1 oversampling). */
hal_status_t bme280_init(const bme280_t *dev, bme280_calib_t *cal);
/** @brief Read and compensate (Bosch integer formulas, BME280 datasheet sec. 4.2.3 / 8.2). */
hal_status_t bme280_read(const bme280_t *dev, const bme280_calib_t *cal, bme280_data_t *out);
""", """
#include "bme280.h"

#define BME_REG_ID        (0xD0U)
#define BME_REG_CALIB_TP  (0x88U)
#define BME_REG_CALIB_H1  (0xA1U)
#define BME_REG_CALIB_H2  (0xE1U)
#define BME_REG_CTRL_HUM  (0xF2U)
#define BME_REG_CTRL_MEAS (0xF4U)
#define BME_REG_CONFIG    (0xF5U)
#define BME_REG_DATA      (0xF7U)
#define BME_CHIP_ID       (0x60U)
#define BME_HUM_X1        (0x01U)
#define BME_MEAS_X1_NORM  (0x27U)
#define BME_CFG_1000MS    (0xA0U)

static hal_status_t rd(const bme280_t *dev, uint8_t reg, uint8_t *buf, size_t n)
{
    return hal_i2c_write_read(dev->i2c, dev->addr, &reg, 1U, buf, n);
}

static hal_status_t wr(const bme280_t *dev, uint8_t reg, uint8_t v)
{
    uint8_t b[2];
    b[0] = reg;
    b[1] = v;
    return hal_i2c_write(dev->i2c, dev->addr, b, sizeof(b));
}

static uint16_t u16le(const uint8_t *b) { return (uint16_t)(((uint16_t)b[1] << 8) | b[0]); }

hal_status_t bme280_init(const bme280_t *dev, bme280_calib_t *cal)
{
    uint8_t b[26];
    uint8_t id = 0U;
    hal_status_t st;
    if ((dev == NULL) || (cal == NULL)) { return HAL_ERR_PARAM; }
    st = rd(dev, BME_REG_ID, &id, 1U);
    if (st != HAL_OK) { return st; }
    if (id != BME_CHIP_ID) { return HAL_ERR_DEVICE; }
    st = rd(dev, BME_REG_CALIB_TP, b, 24U);
    if (st != HAL_OK) { return st; }
    cal->t1 = u16le(&b[0]);  cal->t2 = (int16_t)u16le(&b[2]);  cal->t3 = (int16_t)u16le(&b[4]);
    cal->p1 = u16le(&b[6]);  cal->p2 = (int16_t)u16le(&b[8]);  cal->p3 = (int16_t)u16le(&b[10]);
    cal->p4 = (int16_t)u16le(&b[12]); cal->p5 = (int16_t)u16le(&b[14]); cal->p6 = (int16_t)u16le(&b[16]);
    cal->p7 = (int16_t)u16le(&b[18]); cal->p8 = (int16_t)u16le(&b[20]); cal->p9 = (int16_t)u16le(&b[22]);
    st = rd(dev, BME_REG_CALIB_H1, &cal->h1, 1U);
    if (st == HAL_OK) { st = rd(dev, BME_REG_CALIB_H2, b, 7U); }
    if (st != HAL_OK) { return st; }
    cal->h2 = (int16_t)u16le(&b[0]);
    cal->h3 = b[2];
    cal->h4 = (int16_t)(((int16_t)(int8_t)b[3] * 16) | (int16_t)(b[4] & 0x0FU));
    cal->h5 = (int16_t)(((int16_t)(int8_t)b[5] * 16) | (int16_t)(b[4] >> 4));
    cal->h6 = (int8_t)b[6];
    st = wr(dev, BME_REG_CTRL_HUM, BME_HUM_X1);
    if (st == HAL_OK) { st = wr(dev, BME_REG_CONFIG, BME_CFG_1000MS); }
    if (st == HAL_OK) { st = wr(dev, BME_REG_CTRL_MEAS, BME_MEAS_X1_NORM); }
    return st;
}

hal_status_t bme280_read(const bme280_t *dev, const bme280_calib_t *c, bme280_data_t *out)
{
    uint8_t b[8];
    int32_t adc_p, adc_t, adc_h, v1, v2, t_fine, h;
    uint32_t p;
    hal_status_t st;
    if ((dev == NULL) || (c == NULL) || (out == NULL)) { return HAL_ERR_PARAM; }
    st = rd(dev, BME_REG_DATA, b, sizeof(b));
    if (st != HAL_OK) { return st; }
    adc_p = (int32_t)(((uint32_t)b[0] << 12) | ((uint32_t)b[1] << 4) | ((uint32_t)b[2] >> 4));
    adc_t = (int32_t)(((uint32_t)b[3] << 12) | ((uint32_t)b[4] << 4) | ((uint32_t)b[5] >> 4));
    adc_h = (int32_t)(((uint32_t)b[6] << 8) | b[7]);
    /* temperature */
    v1 = ((((adc_t >> 3) - ((int32_t)c->t1 << 1))) * (int32_t)c->t2) >> 11;
    v2 = (((((adc_t >> 4) - (int32_t)c->t1) * ((adc_t >> 4) - (int32_t)c->t1)) >> 12) * (int32_t)c->t3) >> 14;
    t_fine = v1 + v2;
    out->t_x100 = ((t_fine * 5) + 128) >> 8;
    /* pressure (32-bit formula) */
    v1 = (t_fine >> 1) - 64000L;
    v2 = (((v1 >> 2) * (v1 >> 2)) >> 11) * (int32_t)c->p6;
    v2 = v2 + ((v1 * (int32_t)c->p5) << 1);
    v2 = (v2 >> 2) + ((int32_t)c->p4 << 16);
    v1 = ((((int32_t)c->p3 * (((v1 >> 2) * (v1 >> 2)) >> 13)) >> 3) + (((int32_t)c->p2 * v1) >> 1)) >> 18;
    v1 = ((32768L + v1) * (int32_t)c->p1) >> 15;
    if (v1 == 0) { out->p_pa = 0U; }
    else
    {
        p = ((uint32_t)(1048576L - adc_p) - (uint32_t)(v2 >> 12)) * 3125UL;
        p = (p < 0x80000000UL) ? ((p << 1) / (uint32_t)v1) : ((p / (uint32_t)v1) * 2UL);
        v1 = ((int32_t)c->p9 * (int32_t)(((p >> 3) * (p >> 3)) >> 13)) >> 12;
        v2 = ((int32_t)(p >> 2) * (int32_t)c->p8) >> 13;
        out->p_pa = (uint32_t)((int32_t)p + ((v1 + v2 + (int32_t)c->p7) >> 4));
    }
    /* humidity */
    h = t_fine - 76800L;
    h = ((((adc_h << 14) - ((int32_t)c->h4 << 20) - ((int32_t)c->h5 * h)) + 16384L) >> 15) *
        (((((((h * (int32_t)c->h6) >> 10) * (((h * (int32_t)c->h3) >> 11) + 32768L)) >> 10) + 2097152L) *
          (int32_t)c->h2 + 8192L) >> 14);
    h = h - (((((h >> 15) * (h >> 15)) >> 7) * (int32_t)c->h1) >> 4);
    h = (h < 0) ? 0 : h;
    h = (h > 419430400L) ? 419430400L : h;
    out->rh_x1024 = (uint32_t)(h >> 12);
    return HAL_OK;
}
""")
d("eeprom24", ("i2c", "timer"), _I2C_T.format(what="Microchip 24LC256 I2C EEPROM (32 KiB, 64-byte pages).", t="eeprom24_t") + """
/** @brief Read len bytes from mem_addr. */
hal_status_t eeprom24_read(const eeprom24_t *dev, uint16_t mem_addr, uint8_t *out, size_t len);
/** @brief Write up to one page (must not cross a 64-byte page boundary). */
hal_status_t eeprom24_write_page(const eeprom24_t *dev, uint16_t mem_addr, const uint8_t *data, size_t len);
""", """
#include "eeprom24.h"
#include "hal_timer.h"

#define EE_PAGE_SIZE   (64U)
#define EE_SIZE        (32768UL)
#define EE_TWC_MS      (5U)

hal_status_t eeprom24_read(const eeprom24_t *dev, uint16_t mem_addr, uint8_t *out, size_t len)
{
    uint8_t a[2];
    if ((dev == NULL) || (out == NULL) || (((uint32_t)mem_addr + len) > EE_SIZE)) { return HAL_ERR_PARAM; }
    a[0] = (uint8_t)(mem_addr >> 8);
    a[1] = (uint8_t)mem_addr;
    return hal_i2c_write_read(dev->i2c, dev->addr, a, sizeof(a), out, len);
}

hal_status_t eeprom24_write_page(const eeprom24_t *dev, uint16_t mem_addr, const uint8_t *data, size_t len)
{
    uint8_t buf[EE_PAGE_SIZE + 2U];
    size_t i;
    hal_status_t st;
    if ((dev == NULL) || (data == NULL) || (len == 0U) || (len > EE_PAGE_SIZE) ||
        (((mem_addr % EE_PAGE_SIZE) + len) > EE_PAGE_SIZE)) { return HAL_ERR_PARAM; }
    buf[0] = (uint8_t)(mem_addr >> 8);
    buf[1] = (uint8_t)mem_addr;
    for (i = 0U; i < len; i++) { buf[i + 2U] = data[i]; }
    st = hal_i2c_write(dev->i2c, dev->addr, buf, len + 2U);
    hal_delay_ms(EE_TWC_MS);     /* internal write cycle */
    return st;
}
""")

# --------------------------------------------------------------------------- SPI devices
_SPI_T = """
#include "hal_spi.h"
#include "hal_gpio.h"

/** @brief {what} */
typedef struct
{{
    hal_spi_id_t spi;   /**< bus */
    hal_gpio_t   cs;    /**< chip select, active low */
}} {t};
"""
d("spi_dev", ("spi", "gpio"), _SPI_T.format(what="Generic SPI device.", t="spi_dev_t") + """
/** @brief CS low, transfer a buffer, CS high. */
hal_status_t spi_dev_xfer(const spi_dev_t *dev, const uint8_t *tx, uint8_t *rx, size_t len);
""", """
#include "spi_dev.h"

hal_status_t spi_dev_xfer(const spi_dev_t *dev, const uint8_t *tx, uint8_t *rx, size_t len)
{
    hal_status_t st;
    if (dev == NULL) { return HAL_ERR_PARAM; }
    hal_gpio_write(&dev->cs, false);
    st = hal_spi_transfer_buf(dev->spi, tx, rx, len);
    hal_gpio_write(&dev->cs, true);
    return st;
}
""")
d("flash25", ("spi", "gpio"), _SPI_T.format(what="SPI NOR flash / 25LCxxx EEPROM (JEDEC commands).", t="flash25_t") + """
/** @brief Read the 3-byte JEDEC ID (0x9F). */
hal_status_t flash25_read_id(const flash25_t *dev, uint8_t id[3]);
/** @brief Read len bytes at a 24-bit address. */
hal_status_t flash25_read(const flash25_t *dev, uint32_t addr, uint8_t *out, size_t len);
/** @brief Program within one page (erase first for NOR flash). */
hal_status_t flash25_write(const flash25_t *dev, uint32_t addr, const uint8_t *data, size_t len);
""", """
#include "flash25.h"

#define F25_READ   (0x03U)
#define F25_WRITE  (0x02U)
#define F25_WREN   (0x06U)
#define F25_RDSR   (0x05U)
#define F25_JEDEC  (0x9FU)
#define F25_WIP    (0x01U)
#define F25_POLLS  (60000U)

static void sel(const flash25_t *d, bool on) { hal_gpio_write(&d->cs, !on); }

static void addr24(const flash25_t *d, uint8_t cmd, uint32_t a)
{
    (void)hal_spi_transfer(d->spi, cmd);
    (void)hal_spi_transfer(d->spi, (uint8_t)(a >> 16));
    (void)hal_spi_transfer(d->spi, (uint8_t)(a >> 8));
    (void)hal_spi_transfer(d->spi, (uint8_t)a);
}

hal_status_t flash25_read_id(const flash25_t *dev, uint8_t id[3])
{
    uint8_t i;
    if ((dev == NULL) || (id == NULL)) { return HAL_ERR_PARAM; }
    sel(dev, true);
    (void)hal_spi_transfer(dev->spi, F25_JEDEC);
    for (i = 0U; i < 3U; i++) { id[i] = hal_spi_transfer(dev->spi, 0xFFU); }
    sel(dev, false);
    return HAL_OK;
}

hal_status_t flash25_read(const flash25_t *dev, uint32_t addr, uint8_t *out, size_t len)
{
    hal_status_t st;
    if ((dev == NULL) || (out == NULL)) { return HAL_ERR_PARAM; }
    sel(dev, true);
    addr24(dev, F25_READ, addr);
    st = hal_spi_transfer_buf(dev->spi, NULL, out, len);
    sel(dev, false);
    return st;
}

hal_status_t flash25_write(const flash25_t *dev, uint32_t addr, const uint8_t *data, size_t len)
{
    uint16_t n = 0U;
    uint8_t sr;
    hal_status_t st;
    if ((dev == NULL) || (data == NULL)) { return HAL_ERR_PARAM; }
    sel(dev, true); (void)hal_spi_transfer(dev->spi, F25_WREN); sel(dev, false);
    sel(dev, true);
    addr24(dev, F25_WRITE, addr);
    st = hal_spi_transfer_buf(dev->spi, data, NULL, len);
    sel(dev, false);
    do
    {
        sel(dev, true);
        (void)hal_spi_transfer(dev->spi, F25_RDSR);
        sr = hal_spi_transfer(dev->spi, 0xFFU);
        sel(dev, false);
        n++;
    } while (((sr & F25_WIP) != 0U) && (n < F25_POLLS));
    return (n >= F25_POLLS) ? HAL_ERR_TIMEOUT : st;
}
""")
d("sdcard", ("spi", "gpio"), _SPI_T.format(what="SD / SDHC card in SPI mode.", t="sdcard_t") + """
#define SDCARD_BLOCK  (512U)   /**< bytes per block */

/** @brief Card-select sequence (CMD0, CMD8, ACMD41, CMD58). SPI must run <= 400 kHz here. */
hal_status_t sdcard_init(const sdcard_t *dev, bool *is_sdhc);
/** @brief Read one 512-byte block (block address on SDHC, byte address on SDSC). */
hal_status_t sdcard_read_block(const sdcard_t *dev, uint32_t lba, bool is_sdhc, uint8_t *buf);
""", """
#include "sdcard.h"

#define SD_CMD0     (0U)
#define SD_CMD8     (8U)
#define SD_CMD17    (17U)
#define SD_CMD55    (55U)
#define SD_CMD58    (58U)
#define SD_ACMD41   (41U)
#define SD_R1_IDLE  (0x01U)
#define SD_TOKEN    (0xFEU)
#define SD_TRIES    (2000U)

static uint8_t sd_cmd(const sdcard_t *d, uint8_t cmd, uint32_t arg, uint8_t crc)
{
    uint8_t r = 0xFFU;
    uint16_t n = 0U;
    (void)hal_spi_transfer(d->spi, 0xFFU);
    (void)hal_spi_transfer(d->spi, (uint8_t)(0x40U | cmd));
    (void)hal_spi_transfer(d->spi, (uint8_t)(arg >> 24));
    (void)hal_spi_transfer(d->spi, (uint8_t)(arg >> 16));
    (void)hal_spi_transfer(d->spi, (uint8_t)(arg >> 8));
    (void)hal_spi_transfer(d->spi, (uint8_t)arg);
    (void)hal_spi_transfer(d->spi, crc);
    do { r = hal_spi_transfer(d->spi, 0xFFU); n++; } while (((r & 0x80U) != 0U) && (n < 10U));
    return r;
}

hal_status_t sdcard_init(const sdcard_t *dev, bool *is_sdhc)
{
    uint8_t i, r, ocr[4];
    uint16_t n = 0U;
    if ((dev == NULL) || (is_sdhc == NULL)) { return HAL_ERR_PARAM; }
    hal_gpio_write(&dev->cs, true);
    for (i = 0U; i < 10U; i++) { (void)hal_spi_transfer(dev->spi, 0xFFU); }   /* >= 74 clocks */
    hal_gpio_write(&dev->cs, false);
    if (sd_cmd(dev, SD_CMD0, 0UL, 0x95U) != SD_R1_IDLE) { hal_gpio_write(&dev->cs, true); return HAL_ERR_DEVICE; }
    r = sd_cmd(dev, SD_CMD8, 0x1AAUL, 0x87U);
    for (i = 0U; i < 4U; i++) { ocr[i] = hal_spi_transfer(dev->spi, 0xFFU); }
    do
    {
        (void)sd_cmd(dev, SD_CMD55, 0UL, 0x01U);
        r = sd_cmd(dev, SD_ACMD41, 0x40000000UL, 0x01U);
        n++;
    } while ((r != 0U) && (n < SD_TRIES));
    if (r != 0U) { hal_gpio_write(&dev->cs, true); return HAL_ERR_TIMEOUT; }
    (void)sd_cmd(dev, SD_CMD58, 0UL, 0x01U);
    for (i = 0U; i < 4U; i++) { ocr[i] = hal_spi_transfer(dev->spi, 0xFFU); }
    *is_sdhc = (ocr[0] & 0x40U) != 0U;
    hal_gpio_write(&dev->cs, true);
    return HAL_OK;
}

hal_status_t sdcard_read_block(const sdcard_t *dev, uint32_t lba, bool is_sdhc, uint8_t *buf)
{
    uint16_t n = 0U;
    uint8_t t;
    hal_status_t st = HAL_OK;
    if ((dev == NULL) || (buf == NULL)) { return HAL_ERR_PARAM; }
    hal_gpio_write(&dev->cs, false);
    if (sd_cmd(dev, SD_CMD17, is_sdhc ? lba : (lba * SDCARD_BLOCK), 0x01U) != 0U) { st = HAL_ERR_DEVICE; }
    if (st == HAL_OK)
    {
        do { t = hal_spi_transfer(dev->spi, 0xFFU); n++; } while ((t != SD_TOKEN) && (n < SD_TRIES));
        if (t != SD_TOKEN) { st = HAL_ERR_TIMEOUT; }
    }
    if (st == HAL_OK)
    {
        st = hal_spi_transfer_buf(dev->spi, NULL, buf, SDCARD_BLOCK);
        (void)hal_spi_transfer(dev->spi, 0xFFU);   /* CRC */
        (void)hal_spi_transfer(dev->spi, 0xFFU);
    }
    hal_gpio_write(&dev->cs, true);
    return st;
}
""")
d("mcp2515", ("spi", "gpio"), _SPI_T.format(what="Microchip MCP2515 stand-alone CAN controller.", t="mcp2515_t") + """
/** @brief Reset, set 500 kbit/s for an 8 MHz crystal and enter normal mode. */
hal_status_t mcp2515_init(const mcp2515_t *dev);
/** @brief Send a standard (11-bit) data frame from TX buffer 0. */
hal_status_t mcp2515_send(const mcp2515_t *dev, uint16_t id, const uint8_t *data, uint8_t dlc);
""", """
#include "mcp2515.h"
#include "hal_timer.h"

#define MCP_RESET     (0xC0U)
#define MCP_WRITE     (0x02U)
#define MCP_READ      (0x03U)
#define MCP_RTS_TX0   (0x81U)
#define MCP_CNF3      (0x28U)
#define MCP_CANCTRL   (0x0FU)
#define MCP_CANSTAT   (0x0EU)
#define MCP_TXB0SIDH  (0x31U)
#define MCP_MODE_MASK (0xE0U)
#define MCP_MODE_NORM (0x00U)
#define MCP_MAX_DLC   (8U)
/* 8 MHz, 500 kbit/s: 8 TQ, sample point 75 % */
#define MCP_CNF1_VAL  (0x00U)
#define MCP_CNF2_VAL  (0x90U)
#define MCP_CNF3_VAL  (0x02U)

static void wr(const mcp2515_t *d, uint8_t reg, const uint8_t *v, uint8_t n)
{
    uint8_t i;
    hal_gpio_write(&d->cs, false);
    (void)hal_spi_transfer(d->spi, MCP_WRITE);
    (void)hal_spi_transfer(d->spi, reg);
    for (i = 0U; i < n; i++) { (void)hal_spi_transfer(d->spi, v[i]); }
    hal_gpio_write(&d->cs, true);
}

static uint8_t rd(const mcp2515_t *d, uint8_t reg)
{
    uint8_t v;
    hal_gpio_write(&d->cs, false);
    (void)hal_spi_transfer(d->spi, MCP_READ);
    (void)hal_spi_transfer(d->spi, reg);
    v = hal_spi_transfer(d->spi, 0xFFU);
    hal_gpio_write(&d->cs, true);
    return v;
}

hal_status_t mcp2515_init(const mcp2515_t *dev)
{
    const uint8_t cnf[3] = {MCP_CNF3_VAL, MCP_CNF2_VAL, MCP_CNF1_VAL};   /* CNF3, CNF2, CNF1 are consecutive */
    const uint8_t mode = MCP_MODE_NORM;
    if (dev == NULL) { return HAL_ERR_PARAM; }
    hal_gpio_write(&dev->cs, false);
    (void)hal_spi_transfer(dev->spi, MCP_RESET);
    hal_gpio_write(&dev->cs, true);
    hal_delay_ms(2U);
    wr(dev, MCP_CNF3, cnf, 3U);
    wr(dev, MCP_CANCTRL, &mode, 1U);
    return ((rd(dev, MCP_CANSTAT) & MCP_MODE_MASK) == MCP_MODE_NORM) ? HAL_OK : HAL_ERR_DEVICE;
}

hal_status_t mcp2515_send(const mcp2515_t *dev, uint16_t id, const uint8_t *data, uint8_t dlc)
{
    uint8_t buf[5U + MCP_MAX_DLC];
    uint8_t i;
    if ((dev == NULL) || (dlc > MCP_MAX_DLC) || ((data == NULL) && (dlc != 0U)) || (id > 0x7FFU)) { return HAL_ERR_PARAM; }
    buf[0] = (uint8_t)(id >> 3);
    buf[1] = (uint8_t)((id & 0x07U) << 5);
    buf[2] = 0U;
    buf[3] = 0U;
    buf[4] = dlc;
    for (i = 0U; i < dlc; i++) { buf[5U + i] = data[i]; }
    wr(dev, MCP_TXB0SIDH, buf, (uint8_t)(5U + dlc));
    hal_gpio_write(&dev->cs, false);
    (void)hal_spi_transfer(dev->spi, MCP_RTS_TX0);
    hal_gpio_write(&dev->cs, true);
    return HAL_OK;
}
""")

# --------------------------------------------------------------------------- UART devices
d("uart_dev", ("uart",), """
#include "hal_uart.h"

/** @brief Device on a UART (USB-UART, HC-05, ESP-01, GPS, RS485 ...). */
typedef struct
{
    hal_uart_id_t uart;   /**< port */
} uart_dev_t;

/** @brief Send a string. */
hal_status_t uart_dev_print(const uart_dev_t *dev, const char *str);
/** @brief Send an integer followed by CR LF. */
hal_status_t uart_dev_println_int(const uart_dev_t *dev, int32_t value);
/** @brief Collect a line (ends on LF). @return HAL_OK when complete, HAL_ERR_BUSY while waiting */
hal_status_t uart_dev_read_line(const uart_dev_t *dev, char *buf, size_t size, size_t *len);
""", """
#include "uart_dev.h"

hal_status_t uart_dev_print(const uart_dev_t *dev, const char *str)
{
    return (dev == NULL) ? HAL_ERR_PARAM : hal_uart_write_str(dev->uart, str);
}

hal_status_t uart_dev_println_int(const uart_dev_t *dev, int32_t value)
{
    hal_status_t st;
    if (dev == NULL) { return HAL_ERR_PARAM; }
    st = hal_uart_write_int(dev->uart, value);
    return (st == HAL_OK) ? hal_uart_write_str(dev->uart, "\\r\\n") : st;
}

hal_status_t uart_dev_read_line(const uart_dev_t *dev, char *buf, size_t size, size_t *len)
{
    uint8_t b = 0U;
    if ((dev == NULL) || (buf == NULL) || (len == NULL) || (size < 2U)) { return HAL_ERR_PARAM; }
    while (hal_uart_read_byte(dev->uart, &b) == HAL_OK)
    {
        if (b == (uint8_t)'\\n') { buf[*len] = '\\0'; return HAL_OK; }
        if ((b != (uint8_t)'\\r') && (*len < (size - 1U))) { buf[*len] = (char)b; (*len)++; }
    }
    return HAL_ERR_BUSY;
}
""")

for _n, _why in (("ws2812", "WS2812 needs 0.35/0.9 us bit timing; bit-banging it from C is not cycle-exact on this "
                  "device - use an SPI-encoded or assembly driver. Stub returns HAL_ERR_UNSUPPORTED."),
                 ("usb", "Native USB needs the Microchip USB stack (MCC / MLA); this layout reserves the pins and "
                  "returns HAL_ERR_UNSUPPORTED until the stack is added.")):
    d(_n, (), f"""
#include "hal.h"

/** @brief Placeholder. {_why} */
typedef struct
{{
    uint8_t reserved;   /**< unused */
}} {_n}_t;

/** @brief Not generated - see the file header. */
hal_status_t {_n}_init(const {_n}_t *dev);
""", f"""
#include "{_n}.h"

hal_status_t {_n}_init(const {_n}_t *dev)
{{
    (void)dev;
    return HAL_ERR_UNSUPPORTED;
}}
""")

TYPE_NAME = {"led": "led_t", "buzzer": "buzzer_t", "relay": "relay_t", "motor": "motor_t", "rgb": "rgb_t",
             "ledbar": "ledbar_t", "seg7": "seg7_t", "hc595": "hc595_t", "lcd_hd44780": "lcd_t", "ssd1306": "ssd1306_t",
             "tone": "tone_t", "servo": "servo_t", "hbridge": "hbridge_t", "stepper_uln": "stepper_uln_t",
             "stepper_a4988": "a4988_t", "button": "button_t", "keypad": "keypad_t", "encoder": "encoder_t",
             "pot": "pot_t", "analog_sensor": "analog_sensor_t", "joystick": "joystick_t", "ntc": "ntc_t", "lm35": "lm35_t",
             "dht22": "dht22_t", "ds18b20": "ds18b20_t", "i2c_dev": "i2c_dev_t", "tmp102": "tmp102_t",
             "mpu6050": "mpu6050_t", "bme280": "bme280_t", "eeprom24": "eeprom24_t", "spi_dev": "spi_dev_t",
             "flash25": "flash25_t", "sdcard": "sdcard_t", "mcp2515": "mcp2515_t", "uart_dev": "uart_dev_t",
             "ws2812": "ws2812_t", "usb": "usb_t"}
