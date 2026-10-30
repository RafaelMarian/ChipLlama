"""Component library for the Circuit tab (Simulink-style hardware diagram).

Each part declares its ports and the role each port needs on the MCU side:
  out / in / io   GPIO output, input, bidirectional (1-wire, DHT)
  an              analog input (ANx)
  pwm             output compare / CCP PWM
  rx / tx         UART *device* RX (MCU drives it with UxTX) / device TX (feeds MCU UxRX)
  scl sda         I2C,   sck mosi miso cs   SPI (cs is a GPIO output)
  vcc gnd         supply pins of the part (checked against the rail voltage)
  p               passive terminal (LED, resistor, button ...; resolved through the netlist)
``drv`` names the generated driver module (drivers/<drv>.c/.h); ``None`` = no firmware needed.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Part:
    type: str
    label: str
    cat: str
    prefix: str
    ports: list[tuple[str, str]]
    params: dict = field(default_factory=dict)
    drv: str | None = None
    doc: str = ""
    logic_v: float | None = 3.3      # default I/O voltage of the module (5 V parts trigger the tolerance rule)
    i2c_addr: int | None = None

    def to_dict(self):
        return {"type": self.type, "label": self.label, "cat": self.cat, "prefix": self.prefix,
                "ports": [{"name": n, "role": r} for n, r in self.ports], "params": self.params, "drv": self.drv,
                "doc": self.doc, "logic_v": self.logic_v, "i2c_addr": self.i2c_addr}


P = Part
_LIB = [
    # ---- power
    P("vdd33", "3.3 V rail", "Power", "VDD", [("+", "p")], {"v": 3.3}, None, "Logic supply (VDD 2.0-3.6 V, DS39897C p.311)."),
    P("v5", "5 V rail", "Power", "V5_", [("+", "p")], {"v": 5.0}, None, "5 V supply for motors, relays, 5 V modules."),
    P("gnd", "GND", "Power", "GND", [("-", "p")], {}, None),
    P("battery", "Battery", "Power", "BT", [("+", "p"), ("-", "p")], {"v": 3.7, "mah": 1000}, None),
    P("regulator", "LDO regulator", "Power", "U", [("VIN", "p"), ("VOUT", "p"), ("GND", "p")], {"vout": 3.3, "part": "MCP1700-3302E"}, None,
      "Add 1 uF in / out ceramics."),
    P("usb_conn", "USB connector (native USB)", "Power", "J", [("VBUS", "p"), ("D-", "p"), ("D+", "p"), ("GND", "p")], {}, "usb",
      "Native USB: D+/D- go to the MCU's fixed D+/D- pins; needs a 48 MHz-capable clock (GB110: 96 MHz PLL)."),
    # ---- passives
    P("resistor", "Resistor", "Passive", "R", [("1", "p"), ("2", "p")], {"ohms": 330}, None),
    P("capacitor", "Capacitor", "Passive", "C", [("1", "p"), ("2", "p")], {"farads": 1e-7, "kind": "ceramic"}, None),
    P("diode", "Diode", "Passive", "D", [("A", "p"), ("K", "p")], {"part": "1N4148"}, None, "Flyback / protection diode."),
    P("zener", "Zener diode", "Passive", "DZ", [("A", "p"), ("K", "p")], {"vz": 3.3}, None, "Clamp to the VDD rail."),
    P("crystal", "32.768 kHz crystal (SOSC)", "Passive", "Y", [("1", "p"), ("2", "p")], {"hz": 32768, "cl_pf": 12.5}, None,
      "Secondary oscillator: connect to SOSCI / SOSCO with two load caps."),
    P("pot", "Potentiometer", "Analog", "POT", [("1", "p"), ("W", "an"), ("2", "p")], {"ohms": 10000}, "pot"),
    # ---- semis
    P("npn", "NPN transistor", "Switching", "Q", [("B", "p"), ("C", "p"), ("E", "p")], {"part": "2N2222", "hfe": 100}, None),
    P("pnp", "PNP transistor", "Switching", "Q", [("B", "p"), ("C", "p"), ("E", "p")], {"part": "2N2907", "hfe": 100}, None),
    P("nmos", "N-MOSFET", "Switching", "Q", [("G", "p"), ("D", "p"), ("S", "p")], {"part": "IRLZ44N", "vgs_th": 2.0}, None,
      "Use a logic-level MOSFET (fully on at 3.3 V gate)."),
    P("pmos", "P-MOSFET", "Switching", "Q", [("G", "p"), ("D", "p"), ("S", "p")], {"part": "AO3401", "vgs_th": -1.3}, None),
    P("opto", "Optocoupler", "Switching", "U", [("A", "p"), ("K", "p"), ("C", "p"), ("E", "p")], {"part": "PC817", "if_ma": 5}, None),
    P("relay", "Relay (bare coil)", "Switching", "K", [("COIL+", "p"), ("COIL-", "p"), ("COM", "p"), ("NO", "p")],
      {"coil_v": 5, "coil_ohms": 70}, "relay", "Drive through a transistor + flyback diode."),
    P("relay_module", "Relay module", "Switching", "K", [("IN", "out"), ("VCC", "vcc"), ("GND", "gnd")],
      {"active_low": True}, "relay", "Module with on-board transistor and diode.", 5.0),
    # ---- outputs
    P("led", "LED", "Output", "LED", [("A", "p"), ("K", "p")], {"color": "red", "vf": 2.0, "if_ma": 10}, "led"),
    P("rgb_led", "RGB LED (common cathode)", "Output", "RGB", [("R", "p"), ("G", "p"), ("B", "p"), ("K", "p")],
      {"vf": [2.0, 3.0, 3.0]}, "rgb", "One series resistor per colour; PWM-capable pins allow mixing."),
    P("led_bar", "LED bar (8)", "Output", "BAR", [(f"D{i}", "out") for i in range(8)] + [("K", "gnd")], {"n": 8}, "ledbar",
      "Assumes on-board / array resistors."),
    P("seg7", "7-segment (direct)", "Output", "DS", [(s, "out") for s in "ABCDEFG"] + [("DP", "out"), ("COM", "p")],
      {"common": "cathode"}, "seg7", "Segments need series resistors (array)."),
    P("hc595", "74HC595 + 7-seg", "Output", "U", [("SER", "mosi"), ("SRCLK", "sck"), ("RCLK", "cs"), ("VCC", "vcc"), ("GND", "gnd")],
      {"digits": 1}, "hc595", "Shift register driven from SPI (SDO, SCK, latch on a GPIO)."),
    P("lcd1602", "LCD 16x2 (HD44780, 4-bit)", "Output", "LCD", [("RS", "out"), ("E", "out"), ("D4", "out"), ("D5", "out"),
      ("D6", "out"), ("D7", "out"), ("VCC", "vcc"), ("GND", "gnd")], {"cols": 16, "rows": 2}, "lcd_hd44780",
      "R/W tied to GND (write-only). 5 V modules accept 3.3 V logic as VIH>=2.2 V on most; check yours.", 5.0),
    P("ssd1306", "OLED SSD1306 (I2C)", "Output", "OLED", [("SCL", "scl"), ("SDA", "sda"), ("VCC", "vcc"), ("GND", "gnd")],
      {"w": 128, "h": 64}, "ssd1306", "", 3.3, 0x3C),
    P("buzzer_active", "Buzzer (active)", "Output", "BZ", [("+", "p"), ("-", "p")], {"ma": 25}, "buzzer"),
    P("buzzer_passive", "Buzzer (passive)", "Output", "BZ", [("+", "pwm"), ("-", "gnd")], {"hz": 2000}, "tone"),
    P("ws2812", "WS2812 strip", "Output", "LS", [("DIN", "out"), ("VCC", "vcc"), ("GND", "gnd")], {"n": 8}, "ws2812",
      "800 kHz bit-banged timing; 5 V strip wants a level shifter (VIH 0.7*VDD).", 5.0),
    # ---- motion
    P("servo", "Servo", "Motion", "SERVO", [("SIG", "pwm"), ("V+", "vcc"), ("GND", "gnd")], {"min_us": 1000, "max_us": 2000}, "servo",
      "50 Hz PWM; power from 5 V, signal at 3.3 V is fine.", 3.3),
    P("dc_motor", "DC motor", "Motion", "M", [("+", "p"), ("-", "p")], {"ma": 300}, "motor", "Never from a pin: use a transistor/H-bridge + diode."),
    P("hbridge", "H-bridge (L298N / DRV8833)", "Motion", "U", [("IN1", "out"), ("IN2", "out"), ("EN", "pwm"), ("OUT1", "p"),
      ("OUT2", "p"), ("VM", "p"), ("GND", "gnd")], {"part": "DRV8833"}, "hbridge", "L298N needs VIH 2.3 V: OK from 3.3 V.", 3.3),
    P("stepper_uln", "Stepper + ULN2003", "Motion", "U", [("IN1", "out"), ("IN2", "out"), ("IN3", "out"), ("IN4", "out"),
      ("VM", "p"), ("GND", "gnd")], {"steps": 2048}, "stepper_uln"),
    P("stepper_a4988", "Stepper + A4988", "Motion", "U", [("STEP", "out"), ("DIR", "out"), ("EN", "out"), ("VDD", "vcc"),
      ("VM", "p"), ("GND", "gnd")], {"steps": 200}, "stepper_a4988"),
    # ---- inputs
    P("button", "Push button", "Input", "BTN", [("1", "p"), ("2", "p")], {"pullup": "internal", "debounce_ms": 20}, "button"),
    P("switch", "Switch (SPST)", "Input", "SW", [("1", "p"), ("2", "p")], {"pullup": "internal"}, "button"),
    P("keypad", "Keypad 4x4", "Input", "KP", [(f"R{i}", "out") for i in range(1, 5)] + [(f"C{i}", "in") for i in range(1, 5)],
      {"keys": "123A456B789C*0#D"}, "keypad", "Columns use internal pull-ups; rows driven low one at a time."),
    P("encoder", "Rotary encoder", "Input", "ENC", [("A", "in"), ("B", "in"), ("SW", "in"), ("GND", "gnd")], {}, "encoder"),
    P("joystick", "Joystick (2 axis + button)", "Input", "JOY", [("X", "an"), ("Y", "an"), ("SW", "in"), ("VCC", "vcc"),
      ("GND", "gnd")], {}, "joystick"),
    # ---- sensors
    P("ldr", "LDR (light)", "Sensor", "LDR", [("1", "p"), ("2", "an")], {"r_fixed": 10000}, "analog_sensor", "Divider with a fixed resistor."),
    P("ntc", "NTC thermistor", "Sensor", "TH", [("1", "p"), ("2", "an")], {"r25": 10000, "beta": 3950}, "ntc"),
    P("lm35", "LM35 temperature", "Sensor", "U", [("VOUT", "an"), ("VS", "vcc"), ("GND", "gnd")], {}, "lm35",
      "10 mV/degC; needs VS >= 4 V (use 5 V).", 5.0),
    P("dht22", "DHT22 humidity/temp", "Sensor", "U", [("DATA", "io"), ("VCC", "vcc"), ("GND", "gnd")], {}, "dht22",
      "Single-wire; 10 k pull-up on DATA.", 3.3),
    P("ds18b20", "DS18B20 (1-Wire)", "Sensor", "U", [("DQ", "io"), ("VDD", "vcc"), ("GND", "gnd")], {}, "ds18b20",
      "4.7 k pull-up on DQ."),
    P("tmp102", "TMP102 (I2C)", "Sensor", "U", [("SCL", "scl"), ("SDA", "sda"), ("VCC", "vcc"), ("GND", "gnd")], {"addr": "0x48"},
      "tmp102", "", 3.3, 0x48),
    P("bme280", "BME280 (I2C)", "Sensor", "U", [("SCL", "scl"), ("SDA", "sda"), ("VCC", "vcc"), ("GND", "gnd")], {"addr": "0x76"},
      "bme280", "", 3.3, 0x76),
    P("mpu6050", "MPU-6050 IMU (I2C)", "Sensor", "U", [("SCL", "scl"), ("SDA", "sda"), ("VCC", "vcc"), ("GND", "gnd")],
      {"addr": "0x68"}, "mpu6050", "", 3.3, 0x68),
    P("i2c_sensor", "Generic I2C device", "Sensor", "U", [("SCL", "scl"), ("SDA", "sda"), ("VCC", "vcc"), ("GND", "gnd")],
      {"addr": "0x48"}, "i2c_dev", "", 3.3, 0x48),
    # ---- memory / bus
    P("eeprom24", "24LC256 EEPROM (I2C)", "Memory", "U", [("SCL", "scl"), ("SDA", "sda"), ("VCC", "vcc"), ("GND", "gnd")],
      {"addr": "0x50"}, "eeprom24", "", 3.3, 0x50),
    P("flash25", "25LC / SPI flash", "Memory", "U", [("SCK", "sck"), ("SI", "mosi"), ("SO", "miso"), ("CS", "cs"), ("VCC", "vcc"),
      ("GND", "gnd")], {}, "flash25"),
    P("sdcard", "SD card (SPI)", "Memory", "J", [("SCK", "sck"), ("MOSI", "mosi"), ("MISO", "miso"), ("CS", "cs"), ("VCC", "vcc"),
      ("GND", "gnd")], {}, "sdcard", "3.3 V only; init at <= 400 kHz."),
    P("spi_dev", "Generic SPI device", "Memory", "U", [("SCK", "sck"), ("MOSI", "mosi"), ("MISO", "miso"), ("CS", "cs"),
      ("VCC", "vcc"), ("GND", "gnd")], {"khz": 1000, "mode": 0}, "spi_dev"),
    P("mcp2515", "MCP2515 CAN (SPI)", "Comms", "U", [("SCK", "sck"), ("SI", "mosi"), ("SO", "miso"), ("CS", "cs"), ("INT", "in"),
      ("VCC", "vcc"), ("GND", "gnd")], {"osc_mhz": 8, "kbps": 500}, "mcp2515", "", 5.0),
    # ---- comms
    P("uart_usb", "USB-UART adapter", "Comms", "U", [("RX", "rx"), ("TX", "tx"), ("GND", "gnd")], {"baud": 115200, "io_v": 3.3},
      "uart_dev", "Set the adapter to 3.3 V I/O (or use a 5 V-tolerant RX pin)."),
    P("hc05", "HC-05 Bluetooth", "Comms", "U", [("RXD", "rx"), ("TXD", "tx"), ("VCC", "vcc"), ("GND", "gnd")], {"baud": 9600},
      "uart_dev", "3.3 V logic, 5 V supply."),
    P("esp01", "ESP-01 (ESP8266)", "Comms", "U", [("RX", "rx"), ("TX", "tx"), ("EN", "out"), ("VCC", "vcc"), ("GND", "gnd")],
      {"baud": 115200}, "uart_dev", "3.3 V only, up to 300 mA peaks."),
    P("gps", "GPS (NMEA UART)", "Comms", "U", [("RX", "rx"), ("TX", "tx"), ("VCC", "vcc"), ("GND", "gnd")], {"baud": 9600}, "uart_dev"),
    P("rs485", "RS485 (MAX485)", "Comms", "U", [("DI", "rx"), ("RO", "tx"), ("DE", "out"), ("VCC", "vcc"), ("GND", "gnd")],
      {"baud": 9600}, "uart_dev", "MAX485 is 5 V; use MAX3485 for 3.3 V.", 5.0),
]
LIB = {p.type: p for p in _LIB}

ROLE_FN = {   # MCU-side function a device port role needs
    "out": "GPIO_OUT", "in": "GPIO_IN", "io": "GPIO_IO", "an": "AN", "pwm": "OC", "rx": "UTX", "tx": "URX",
    "scl": "SCL", "sda": "SDA", "sck": "SCKOUT", "mosi": "SDO", "miso": "SDI", "cs": "GPIO_OUT",
}


def library() -> list[dict]:
    return [p.to_dict() for p in _LIB]


def drivers() -> list[str]:
    return sorted({p.drv for p in _LIB if p.drv})
