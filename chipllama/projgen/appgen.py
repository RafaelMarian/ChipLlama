"""Demo app/main.c generated from the circuit bindings (super-loop, millisecond scheduling)."""
from __future__ import annotations


def render_main(p) -> str:
    by = {}
    for cid, drv, t, init, ram, extra in p.inst:
        by.setdefault(drv, []).append(cid)
    first = lambda *ds: next((by[d][0] for d in ds if d in by), None)
    has = lambda m: f"hal/hal_{m}.h" in p.files
    uart = None
    if "uart_dev" in by:
        uart = by["uart_dev"][0]
    inc = ['#include "board.h"', '#include "hal_timer.h"'] + [f'#include "hal_{m}.h"' for m in ("uart", "i2c", "spi", "adc", "pwm") if has(m)]
    L = [*inc, "", "#define APP_REPORT_MS       (1000UL)   /**< sensor report period */",
         "#define APP_BLINK_MS        (500UL)    /**< heartbeat period */",
         "#define APP_LOOP_MS         (1U)       /**< super-loop pace */",
         "#define APP_PERMILLE_MAX    (1000U)", "#define APP_SERVO_MAX_DEG   (180U)", ""]
    if "bme280" in by:
        L += [f"static bme280_calib_t s_bme_cal[{len(by['bme280'])}];", ""]
    if uart:
        L += ["/** @brief Print `label value` on the first UART device. */",
              "static void app_report(const char *label, int32_t value)", "{",
              f"    (void)uart_dev_print(&{uart}, label);", f"    (void)uart_dev_println_int(&{uart}, value);", "}", ""]
    else:
        L += ["/** @brief No UART device in the circuit: readings are only shown on displays (if any). */",
              "static void app_report(const char *label, int32_t value)", "{", "    (void)label;", "    (void)value;", "}", ""]
    L += ["/** @brief Show a value on the circuit's displays. */", "static void app_show(int32_t value)", "{"]
    for c in by.get("lcd_hd44780", []):
        L += [f"    lcd_goto(&{c}, 0U, 1U);", f"    lcd_print(&{c}, \"      \");", f"    lcd_goto(&{c}, 0U, 1U);", f"    lcd_print_int(&{c}, value);"]
    for c in by.get("ssd1306", []):
        L += [f"    (void)ssd1306_print_int(&{c}, 0U, 2U, value);"]
    for c in by.get("seg7", []):
        L += [f"    (void)seg7_show(&{c}, (uint8_t)((uint32_t)((value < 0) ? -value : value) % 10UL));"]
    for c in by.get("hc595", []):
        L += [f"    (void)hc595_show_digit(&{c}, (uint8_t)((uint32_t)((value < 0) ? -value : value) % 10UL));"]
    L += ["    (void)value;", "}", ""]

    L += ["int main(void)", "{", "    uint32_t now;", "    uint32_t last_report = 0UL;", "    uint32_t last_blink = 0UL;",
          "    uint16_t level = 0U;", "    hal_status_t st;", ""]
    if "sdcard" in by:
        L.insert(L.index("    hal_status_t st;"), "    bool sd_hc = false;")
    if "mpu6050" in by:
        L.insert(L.index("    hal_status_t st;"), "    mpu6050_sample_t imu;")
    if "bme280" in by:
        L.insert(L.index("    hal_status_t st;"), "    bme280_data_t env;")
    if any(d in by for d in ("tmp102", "lm35", "ntc", "ds18b20", "dht22", "joystick")):
        L.insert(L.index("    hal_status_t st;"), "    int16_t t = 0;")
    if "dht22" in by:
        L.insert(L.index("    hal_status_t st;"), "    uint16_t rh = 0U;")
    if "joystick" in by:
        L.insert(L.index("    hal_status_t st;"), "    int16_t jy = 0;")
    L += ["    board_init();", "    hal_timer_init();"]
    for m, idk in (("uart", "UART"), ("i2c", "I2C"), ("spi", "SPI")):
        if has(m):
            L += [f"    for (uint8_t i = 0U; i < (uint8_t)HAL_{idk}_COUNT; i++)", "    {",
                  f"        (void)hal_{m}_init((hal_{m}_id_t)i);", "    }"]
    if has("adc"):
        L.append("    (void)hal_adc_init();")
    if has("pwm"):
        L.append("    (void)hal_pwm_init();")
    for c in by.get("tone", []):
        L.append(f"    (void)tone_off(&{c});")
    for c in by.get("lcd_hd44780", []):
        L += [f"    lcd_init(&{c});", f"    lcd_print(&{c}, \"ChipLlama\");"]
    for c in by.get("ssd1306", []):
        L += [f"    (void)ssd1306_init(&{c});", f"    (void)ssd1306_clear(&{c});"]
    for c in by.get("mpu6050", []):
        L.append(f"    (void)mpu6050_init(&{c});")
    for i, c in enumerate(by.get("bme280", [])):
        L.append(f"    (void)bme280_init(&{c}, &s_bme_cal[{i}]);")
    for c in by.get("mcp2515", []):
        L.append(f"    (void)mcp2515_init(&{c});")
    for c in by.get("sdcard", []):
        L += [f"    st = sdcard_init(&{c}, &sd_hc);", "    app_report(\"sd \", (int32_t)st);"]
    for c in by.get("ws2812", []) + by.get("usb", []):
        drv = "ws2812" if c in by.get("ws2812", []) else "usb"
        L.append(f"    (void){drv}_init(&{c});")
    if uart:
        L.append(f"    (void)uart_dev_print(&{uart}, \"ChipLlama {p.device} ready\\r\\n\");")
    L += ["", "    for (;;)", "    {", "        now = hal_millis();", "        st = HAL_OK;"]

    outs = [(d, c) for d in ("led", "relay", "buzzer", "motor") for c in by.get(d, [])]
    # inputs -> outputs
    for i, c in enumerate(by.get("button", [])):
        if outs:
            d, o = outs[i % len(outs)]
            L += [f"        if (button_pressed(&{c}))", "        {", f"            {d}_toggle(&{o});", f"            app_report(\"{c} \", 1);", "        }"]
        else:
            L += [f"        if (button_pressed(&{c}))", "        {", f"            app_report(\"{c} \", 1);", "        }"]
    for c in by.get("keypad", []):
        L += ["        {", f"            char key = keypad_scan(&{c});", "            if (key != '\\0')", "            {",
              "                app_report(\"key \", (int32_t)key);", "            }", "        }"]
    for c in by.get("encoder", []):
        L += [f"        (void)encoder_poll(&{c});"]
    pot = first("pot")
    if pot:
        L += [f"        st = pot_read_permille(&{pot}, &level);"]
    elif "joystick" in by:
        L += [f"        st = joystick_read(&{by['joystick'][0]}, &t, &jy);",
              "        level = (uint16_t)((t < 0) ? 0 : t);"]
    if pot or "joystick" in by:
        L += ["        if (st == HAL_OK)", "        {"]
        for c in by.get("servo", []):
            L.append(f"            (void)servo_set(&{c}, (uint16_t)(((uint32_t)level * APP_SERVO_MAX_DEG) / APP_PERMILLE_MAX));")
        for c in by.get("hbridge", []):
            L.append(f"            (void)hbridge_drive(&{c}, (int16_t)((int16_t)(level * 2U) - (int16_t)APP_PERMILLE_MAX));")
        for c in by.get("ledbar", []):
            L.append(f"            ledbar_level(&{c}, (uint8_t)((level + 99U) / 100U));")
        for c in by.get("rgb", []):
            L.append(f"            rgb_set(&{c}, (uint8_t)(level / 143U));")
        if L[-1] == "        {":
            L[-2:] = ["        (void)st;"]
        else:
            L += ["        }"]
    # heartbeat
    hb = [(d, c) for d, c in outs if d == "led"]
    if not by.get("button") and hb:
        L += ["        if ((now - last_blink) >= APP_BLINK_MS)", "        {", "            last_blink = now;", f"            led_toggle(&{hb[0][1]});", "        }"]
    else:
        L += ["        (void)last_blink;"]
    # report
    L += ["        if ((now - last_report) >= APP_REPORT_MS)", "        {", "            last_report = now;"]
    if pot or "joystick" in by:
        L += ["            app_report(\"level \", (int32_t)level);"]
    for c in by.get("tmp102", []):
        L += [f"            if (tmp102_read(&{c}, &t) == HAL_OK) {{ app_report(\"tmp102_c_x16 \", t); app_show(t / 16); }}"]
    for c in by.get("lm35", []):
        L += [f"            if (lm35_read_c_x10(&{c}, &t) == HAL_OK) {{ app_report(\"lm35_c_x10 \", t); app_show(t / 10); }}"]
    for c in by.get("ntc", []):
        L += [f"            if (ntc_read_c_x10(&{c}, &t) == HAL_OK) {{ app_report(\"ntc_c_x10 \", t); app_show(t / 10); }}"]
    for c in by.get("ds18b20", []):
        L += [f"            if (ds18b20_read(&{c}, &t) == HAL_OK) {{ app_report(\"ds18b20_c_x16 \", t); app_show(t / 16); }}"]
    for c in by.get("dht22", []):
        L += [f"            if (dht22_read(&{c}, &t, &rh) == HAL_OK) {{ app_report(\"dht_c_x10 \", t); app_report(\"dht_rh_x10 \", (int32_t)rh); }}"]
    for c in by.get("analog_sensor", []):
        L += ["            {", "                uint16_t a = 0U;", f"                if (analog_sensor_permille(&{c}, &a) == HAL_OK) {{ app_report(\"{c} \", (int32_t)a); }}", "            }"]
    for c in by.get("mpu6050", []):
        L += [f"            if (mpu6050_read(&{c}, &imu) == HAL_OK) {{ app_report(\"ax \", imu.ax); app_report(\"ay \", imu.ay); app_report(\"az \", imu.az); }}"]
    for i, c in enumerate(by.get("bme280", [])):
        L += [f"            if (bme280_read(&{c}, &s_bme_cal[{i}], &env) == HAL_OK) {{ app_report(\"t_x100 \", env.t_x100); "
              f"app_report(\"p_pa \", (int32_t)env.p_pa); app_show(env.t_x100 / 100); }}"]
    for c in by.get("encoder", []):
        L += [f"            app_report(\"enc \", *{c}.count);"]
    for c in by.get("tone", []):
        L += [f"            (void)tone_on(&{c});", "            hal_delay_ms(50U);", f"            (void)tone_off(&{c});"]
    if not any(d in by for d in ("tmp102", "lm35", "ntc", "ds18b20", "dht22", "mpu6050", "bme280")):
        L += ["            app_show((int32_t)(now / APP_REPORT_MS));"]
    L += ["        }", "        (void)st;", "        (void)level;", "        hal_delay_ms(APP_LOOP_MS);", "    }", "}"]
    return "\n".join(L)
