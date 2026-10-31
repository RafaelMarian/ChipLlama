"""MPLAB X project generator for the Circuit tab (deterministic, no AI).

build(circuit_dict) -> {files: {path: text}, tree, device, compiler, backend, verified, findings, notes, amalg}

Layout (HAL style):
    config/config_bits.c        #pragma config from Microchip EDC data
    hal/hal.h hal_config.h      status codes, register width, every timing constant (from the solvers)
    hal/hal_gpio|timer|uart|i2c|spi|adc|pwm.[ch]
    drivers/<driver>.[ch]       one per component kind, written only against the HAL
    board/board.[ch]            pin map + one const instance per placed component, board_init()
    app/main.c                  demo application generated from the circuit
    Makefile, nbproject/        command-line build and MPLAB X project
"""
from __future__ import annotations

import gzip
import io
import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path

from .. import circuit as circuitmod, components as C, edc, pragmas, uart as uartmod
from . import backends as B, hal_api as H
from .drivers import DRIVERS, TYPE_NAME

DATA = Path(__file__).resolve().parent.parent / "data" / "edc"
PORT_RE = re.compile(r"^R([A-K])(\d+)$")


@lru_cache(maxsize=16)
def load_dev(name: str):
    for f in DATA.glob("*.json.gz"):
        if f.name.split(".")[0].upper() == name.upper():
            return edc.Device.from_json(json.load(gzip.open(f)))
    return None


def _num(fn, default=1):
    m = re.search(r"(\d+)", fn or "")
    return int(m.group(1)) if m else default


def _cid(part_id: str) -> str:
    s = re.sub(r"\W", "_", part_id).lower()
    return s if not s[:1].isdigit() else "p_" + s


class Project:
    def __init__(self, d: dict, ics: str = "PGx2", packs: str = ""):
        self.d = d
        self.ics = ics
        self.res = circuitmod.check(d, packs)
        self.device = d.get("device", "PIC24FJ256GB110")
        self.dev = load_dev(self.device)
        self.kind = B.kind(self.dev)
        self.comp = self.dev.compiler if self.dev else "xc16"
        self.fosc, self.fcy, self.cfg_over, self.clock_lines, self.clock_desc = B.clock(self.device.upper())
        self.notes: list[str] = []
        self.findings: list[dict] = [f for f in self.res["findings"] if f["severity"] == "error"]
        self.files: dict[str, str] = {}
        self.cfg: list[str] = []
        self.binds = [b for b in self.res["bindings"] if b.get("drv")]
        self.gpio: dict[str, dict] = {}       # "RC2" -> {out, pullup, analog, an, cn}
        self.uarts, self.i2cs, self.spis, self.pwms, self.adcs = {}, set(), {}, [], set()
        self.specs: dict = {}
        self.inst: list[tuple] = []           # (cid, drv, type, initializer, ram_lines)
        self.mods: set = set()

    # ------------------------------------------------------------------ pin collection
    def pin(self, name, out, pullup=False, analog=False, an=None, cn=None, periph=False):
        g = self.gpio.setdefault(name, {"out": out, "pullup": False, "analog": False, "an": an, "cn": cn, "periph": periph})
        g["pullup"] |= pullup
        g["analog"] |= analog

    def _pwm_of(self, b):
        pw = next((x for x in self.pwms if x["bind"] is b), None)
        if pw is None:      # part not wired to a PWM-capable pin (e.g. device without CCP/OC): inert channel 0
            self.notes.append(f"{b['id']}: no PWM pin connected on this device; its PWM output is inert.")
            pw = {"id": 0}
        return pw

    def collect(self):
        for b in self.binds:
            drv = b["drv"]
            if drv not in DRIVERS:
                continue
            self.mods |= set(DRIVERS[drv][0])
            for port, p in b["pins"].items():
                role, fn, name = p.get("role"), p.get("fn") or "", p.get("port_name")
                if not name:
                    continue
                if role == "an":
                    self.pin(name, False, analog=True, an=p.get("an"), cn=p.get("cn"))
                    if p.get("an") is not None:
                        self.adcs.add(p["an"])
                elif role in ("scl", "sda"):
                    self.i2cs.add(_num(fn))
                    self.pin(name, False, an=p.get("an"), periph=True)
                elif role in ("sck", "mosi", "miso", "so", "si", "sdo", "sdi") or fn.startswith(("SCK", "SDO", "SDI")):
                    n = _num(fn)
                    bus = self.spis.setdefault(n, {"hz": 1e6, "mode": 0})
                    key = "sck" if fn.startswith("SCK") else "sdo" if fn.startswith("SDO") else "sdi"
                    bus[key] = p.get("rp") if self.kind == "pic24" else name
                    bus[key + "_pin"] = name
                    self.pin(name, key != "sdi", an=p.get("an"), periph=True)
                elif role in ("rx", "tx") or re.match(r"^U?\d?(TX|RX)\d?$", fn):
                    n = b.get("uart") or _num(fn)
                    u = self.uarts.setdefault(n, {"baud": int(b["params"].get("baud", 115200))})
                    is_tx = "TX" in fn
                    u["tx" if is_tx else "rx"] = p.get("rp") if self.kind == "pic24" else name
                    self.pin(name, is_tx and self.kind != "pic18", an=p.get("an"), periph=True)
                elif role == "pwm":
                    self.pwms.append({"bind": b, "port": port, "pin": name, "rp": p.get("rp"), "fn": fn})
                    self.pin(name, True, an=p.get("an"), periph=True)
                else:
                    out = role in ("out", "cs")
                    pull = (not out) and (b["params"].get("pullup", "internal") == "internal" if drv == "button" else
                                          drv in ("keypad", "encoder", "joystick"))
                    self.pin(name, out, pullup=pull and role != "io", an=p.get("an"), cn=p.get("cn"))
            if drv in ("sdcard",):
                for bus in self.spis.values():
                    bus["hz"] = min(bus["hz"], 400e3)
            if drv == "spi_dev":
                for bus in self.spis.values():
                    bus["hz"] = min(bus["hz"], float(b["params"].get("khz", 1000)) * 1e3)
                    bus["mode"] = int(b["params"].get("mode", 0))

    # ------------------------------------------------------------------ HAL
    def _ids(self, kind, names):
        en = [f"    HAL_{kind.upper()}_{n} = {i}," for i, n in enumerate(names)]
        return (f"/** @brief {kind.upper()} instances used by this board. */\ntypedef enum\n{{\n" + "\n".join(en)
                + f"\n    HAL_{kind.upper()}_COUNT = {len(names)}\n}} hal_{kind}_id_t;")

    def hal(self):
        dn = self.device
        hdr = lambda n, b, body: H.header(n, b, body, dn)
        self.files["hal/hal.h"] = hdr("hal.h", "Common HAL types and status codes.",
                                      H.HAL_H.format(reg_t=B.reg_t(self.dev), fcy=int(self.fcy)))
        self.files["hal/hal_gpio.h"] = hdr("hal_gpio.h", "Digital I/O on a pin descriptor.", H.GPIO_H)
        self.files["hal/hal_gpio.c"] = H.src("hal_gpio.c", "Digital I/O.", H.GPIO_C, dn)
        self.files["hal/hal_timer.h"] = hdr("hal_timer.h", "Millisecond tick and busy-wait delays.", H.TIMER_H)
        self.files["hal/hal_timer.c"] = H.src("hal_timer.c", "Tick / delays: " + self.clock_desc,
                                              B.timer_c(self.kind, self.dev, self.fcy, self.fosc, self.cfg), dn)
        pin_in, pin_out = [], []
        k = self.kind
        pic18_ccp = {}
        if k != "pic24":
            for i, pw in enumerate(self.pwms):
                pic18_ccp[id(pw)] = _num(pw["fn"]) or i + 1
            if any(pw["bind"]["drv"] == "servo" for pw in self.pwms):
                self.notes.append("Servo PWM needs 50 Hz; PIC18 CCP PWM on Timer2 cannot go below ~244 Hz at 16 MHz. "
                                  "The servo channel is generated but drives a tone-rate PWM: use a PIC24 or a soft-PWM.")
        mods = {
            "uart": (sorted(self.uarts), lambda: B.pic24_uart(self.uarts, self.fcy, self.cfg, None, pin_in, pin_out, self.specs)
                     if k == "pic24" else B.pic18_uart(self.uarts, self.fosc, self.cfg)),
            "i2c": (sorted(self.i2cs), lambda: B.pic24_i2c(self.i2cs, self.fcy, self.cfg, self.specs)
                    if k == "pic24" else B.pic18_i2c(self.i2cs, self.fosc, self.cfg)),
            "spi": (sorted(self.spis), lambda: B.pic24_spi(self.spis, self.fcy, self.cfg, pin_in, pin_out, self.specs)
                    if k == "pic24" else B.pic18_spi(self.spis, self.fosc, self.cfg)),
            "adc": ([], lambda: B.pic24_adc(self.adcs, self.fcy, self.dev, self.cfg, self.specs)
                    if k == "pic24" else B.pic18_adc(self.cfg)),
        }
        # PWM ids
        if k == "pic24":
            pw_names = [f"OC{i + 1}" for i in range(len(self.pwms))]
        else:
            pw_names = [f"CCP{pic18_ccp[id(pw)]}" for pw in self.pwms]
        for pw, nm in zip(self.pwms, pw_names):
            pw["id"] = f"HAL_PWM_{nm}"
        pwm_ok = k == "pic18" or (k == "pic24" and "OC1CON1" in self.dev.sfrs)
        mods["pwm"] = (pw_names, (lambda: B.pic24_pwm(self._pwm_chans(), self.fcy, self.cfg, pin_out, self.specs)) if k == "pic24"
                       else (lambda: B.pic18_pwm([dict(c, ccp=pic18_ccp[id(pw)]) for c, pw in zip(self._pwm_chans(), self.pwms)],
                                                 self.fosc, self.cfg, self.notes)))
        for m, (ids, gen) in mods.items():
            if m not in self.mods and not (m == "uart" and self.uarts):
                continue
            hname = getattr(H, f"{m.upper()}_H")
            body = hname.format(ids=self._ids(m, [str(i) if m != "pwm" else i for i in ids])) if "{ids}" in hname else hname
            self.files[f"hal/hal_{m}.h"] = hdr(f"hal_{m}.h", f"{m.upper()} driver API.", body)
            supported = k in ("pic24", "pic18") and (m != "pwm" or pwm_ok) and (ids or (m == "adc" and self.adcs))
            if supported:
                c = gen()
            else:
                c = B.stub_c(m, None, B.STUBS[m], self.dev)
                self.notes.append(f"hal_{m}: not generated for {self.device} ({self.kind} back end) - "
                                  "functions return HAL_ERR_UNSUPPORTED.")
            self.files[f"hal/hal_{m}.c"] = H.src(f"hal_{m}.c", f"{m.upper()} for {self.device}.", c, dn)
        self.pps = (pin_in, pin_out)

    def _pwm_chans(self):
        out = []
        for i, pw in enumerate(self.pwms):
            b = pw["bind"]
            drv = b["drv"]
            hz = 50 if drv == "servo" else float(b["params"].get("hz", 2000 if drv == "tone" else 1000))
            duty = (float(b["params"].get("min_us", 1000)) + float(b["params"].get("max_us", 2000))) / 2 / 20000 if drv == "servo" else (
                0.5 if drv == "tone" else 0.0)
            out.append({"oc": i + 1, "rp": pw["rp"], "hz": hz, "duty": max(duty, 0.0), "name": f"{b['id']}.{pw['port']}"})
        return out

    # ------------------------------------------------------------------ board
    def _field_regs(self):
        """(register, bit) writes for board_init, grouped by register -> (set_mask, clear_mask)."""
        ops: dict[str, list[int]] = {}
        s = self.dev.sfrs if self.dev else {}

        def op(reg, bit, val):
            if reg not in s:
                return False
            m = ops.setdefault(reg, [0, 0])
            m[0 if val else 1] |= 1 << bit
            return True

        fam = self.kind if self.kind != "basic" else (self.dev.family if self.dev else "")
        for name, g in sorted(self.gpio.items()):
            m = PORT_RE.match(name)
            if not m:
                continue
            P, bit = m.group(1), int(m.group(2))
            an = g["an"]
            analog = g["analog"]
            # analog / digital select
            if f"ANSEL{P}" in s:
                op(f"ANSEL{P}", bit, analog)
            elif an is not None and ("AD1PCFGL" in s or "AD1PCFG" in s):
                reg = "AD1PCFGL" if "AD1PCFGL" in s else "AD1PCFG"
                if an >= 16 and "AD1PCFGH" in s:
                    reg, an = "AD1PCFGH", an - 16
                if not (analog and self.kind == "pic24"):     # pic24 ADC init writes the whole register itself
                    op(reg, an, not analog)
            if g["periph"] and self.kind == "pic18" and not g["out"]:
                op(f"TRIS{P}", bit, 1)
                continue
            if g["periph"] and self.kind == "pic24" and name in self._fixed_i2c():
                continue
            if g["out"]:
                op(f"LAT{P}" if f"LAT{P}" in s else f"PORT{P}", bit, 0)
                op(f"TRIS{P}", bit, 0)
            else:
                op(f"TRIS{P}", bit, 1)
            if g["pullup"]:
                cn = _num(g["cn"]) if g["cn"] is not None else None
                ok = False
                if self.kind == "pic24" and cn is not None:
                    ok = op(f"CNPU{cn // 16 + 1}", cn % 16, 1)
                elif f"CNPU{P}" in s:
                    ok = op(f"CNPU{P}", bit, 1)
                elif f"WPU{P}" in s:
                    ok = op(f"WPU{P}", bit, 1)
                elif "CNPUE" in s and cn is not None:
                    ok = op("CNPUE", cn, 1)
                if not ok:
                    self.notes.append(f"{name}: no internal pull-up on this pin/device - fit an external 10 k pull-up.")
        if "WPUB" in ops and "INTCON2" in s:
            fld = next((f.name for f in s["INTCON2"].fields if "RBPU" in f.name), None)
            if fld:
                bit = next(f.lsb for f in s["INTCON2"].fields if f.name == fld)
                op("INTCON2", bit, 0)
        if self.device.upper() == "PIC16F877A" and "ADCON1" in s:
            ops["ADCON1"] = [0x06, 0xFF ^ 0x06]
        return ops

    def _fixed_i2c(self):
        return {n for n, g in self.gpio.items() if g["periph"] and any(
            p.get("port_name") == n and p.get("role") in ("scl", "sda") for b in self.binds for p in b["pins"].values())}

    def _gp(self, p):
        m = PORT_RE.match(p.get("port_name") or "")
        return f"BOARD_PIN({m.group(1)}, {m.group(2)})" if m else "BOARD_PIN_NONE"

    def _instance(self, b):
        drv, P, pr = b["drv"], b["pins"], b["params"]
        cid = _cid(b["id"])
        g = lambda k: self._gp(P[k]) if k in P else "BOARD_PIN_NONE"
        ah = lambda k: "true" if P.get(k, {}).get("active_high", True) else "false"
        bus = lambda kind, k: f"HAL_{kind.upper()}_{_num(P[k]['fn'])}" if k in P else f"HAL_{kind.upper()}_0"
        an = lambda k: f"{P[k]['an']}U" if k in P and P[k].get("an") is not None else "0U"
        addr = lambda: f"0x{int(str(pr.get('addr', '0x50')), 0):02X}U"
        ram, extra = [], []
        first = next(iter(P), None)
        if drv in ("led", "buzzer", "relay", "motor"):
            k = "CTL" if "CTL" in P else first
            hi = P[k]["active_high"] if k else True
            if str(pr.get("active_low", "")).lower() in ("true", "1", "yes"):
                hi = False
            init = f"{{ {g(k)}, {'true' if hi else 'false'} }}"
        elif drv == "rgb":
            init = f"{{ {g('R')}, {g('G')}, {g('B')}, {ah('R')} }}"
        elif drv == "ledbar":
            ks = [k for k in ("D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7") if k in P]
            extra.append(f"static const hal_gpio_t s_{cid}_pins[] = {{ {', '.join(g(k) for k in ks)} }};")
            init = f"{{ s_{cid}_pins, {len(ks)}U }}"
        elif drv == "seg7":
            ks = ["A", "B", "C", "D", "E", "F", "G", "DP"]
            extra.append(f"static const hal_gpio_t s_{cid}_seg[] = {{ {', '.join(g(k) for k in ks)} }};")
            init = f"{{ s_{cid}_seg, {'true' if str(pr.get('common', 'cathode')).startswith('a') else 'false'} }}"
        elif drv == "hc595":
            init = f"{{ {bus('spi', 'SER')}, {g('RCLK')} }}"
        elif drv == "lcd_hd44780":
            init = (f"{{ {g('RS')}, {g('E')}, {g('D4')}, {g('D5')}, {g('D6')}, {g('D7')}, "
                    f"{int(pr.get('cols', 16))}U, {int(pr.get('rows', 2))}U }}")
        elif drv in ("ssd1306", "i2c_dev", "tmp102", "mpu6050", "bme280", "eeprom24"):
            init = f"{{ {bus('i2c', 'SCL')}, {addr()} }}"
        elif drv in ("tone", "servo"):
            pw = self._pwm_of(b)
            init = (f"{{ {pw['id']}, {int(pr.get('min_us', 1000))}U, {int(pr.get('max_us', 2000))}U }}" if drv == "servo"
                    else f"{{ {pw['id']} }}")
        elif drv == "hbridge":
            pw = self._pwm_of(b)
            init = f"{{ {g('IN1')}, {g('IN2')}, {pw['id']} }}"
        elif drv == "stepper_uln":
            ram.append(f"static uint8_t s_{cid}_phase = 0U;")
            init = f"{{ {{ {g('IN1')}, {g('IN2')}, {g('IN3')}, {g('IN4')} }}, &s_{cid}_phase }}"
        elif drv == "stepper_a4988":
            init = f"{{ {g('STEP')}, {g('DIR')}, {g('EN')} }}"
        elif drv == "button":
            k = "IN" if "IN" in P else first
            ram.append(f"static button_state_t s_{cid}_state;")
            init = f"{{ {g(k)}, {ah(k)}, {int(pr.get('debounce_ms', 20))}U, &s_{cid}_state }}"
        elif drv == "keypad":
            keys = str(pr.get("keys", "123A456B789C*0#D"))[:16].ljust(16, "?")
            init = (f"{{ {{ {', '.join(g(f'R{i}') for i in range(1, 5))} }}, {{ {', '.join(g(f'C{i}') for i in range(1, 5))} }}, "
                    f"\"{keys}\" }}")
        elif drv == "encoder":
            ram += [f"static uint8_t s_{cid}_last = 0U;", f"static int32_t s_{cid}_count = 0L;"]
            init = f"{{ {g('A')}, {g('B')}, {g('SW')}, &s_{cid}_last, &s_{cid}_count }}"
        elif drv in ("pot", "analog_sensor", "lm35"):
            k = next((k for k, p in P.items() if p.get("role") == "an"), first)
            init = f"{{ {an(k)} }}"
        elif drv == "ntc":
            k = next((k for k, p in P.items() if p.get("role") == "an"), first)
            init = (f"{{ {an(k)}, {int(float(pr.get('r_fixed', 10000)))}UL, {int(float(pr.get('r25', 10000)))}UL, "
                    f"{int(float(pr.get('beta', 3950)))}U }}")
        elif drv == "joystick":
            init = f"{{ {an('X')}, {an('Y')}, {g('SW')} }}"
        elif drv in ("dht22", "ds18b20"):
            init = f"{{ {g(first)} }}"
        elif drv in ("spi_dev", "flash25", "sdcard", "mcp2515"):
            sck = next((k for k, p in P.items() if (p.get("fn") or "").startswith("SCK")), None)
            init = f"{{ {bus('spi', sck) if sck else 'HAL_SPI_0'}, {g('CS')} }}"
        elif drv == "uart_dev":
            init = f"{{ HAL_UART_{b.get('uart') or 1} }}"
        else:
            init = "{ 0U }"
        return cid, drv, TYPE_NAME[drv], init, ram, extra

    def board(self):
        dn = self.device
        incs = sorted({f'#include "{d}.h"' for d in self.used_drivers()})
        lat = "PORT##port" if (self.dev and "LATC" not in self.dev.sfrs and "LATB" not in self.dev.sfrs) else "LAT##port"
        insts = [self._instance(b) for b in self.binds if b["drv"] in DRIVERS]
        self.inst = insts
        h = [*incs, "",
             "/** @brief Pin descriptor initialiser for port letter + bit (TRIS / LAT / PORT registers). */",
             f"#define BOARD_PIN(port, bit)  {{ &TRIS##port, &{lat}, &PORT##port, (hal_reg_t)(1UL << (bit)) }}",
             "/** @brief Unconnected pin (driver must not touch it). */",
             "#define BOARD_PIN_NONE        { NULL, NULL, NULL, 0U }", ""]
        for cid, drv, t, init, ram, extra in insts:
            b = next(x for x in self.binds if _cid(x["id"]) == cid)
            pins = ", ".join(f"{k}={p.get('port_name')}" for k, p in b["pins"].items())
            h.append(f"/** @brief {b['id']} ({b['type']}): {pins or 'bus device'}. */")
            h.append(f"extern const {t} {cid};")
        h += ["", "/** @brief Clock, analog/digital select, directions, pull-ups and PPS - call first in main(). */",
              "void board_init(void);"]
        self.files["board/board.h"] = H.header("board.h", "Board pin map and component instances.", "\n".join(h), dn)
        c = [B.INC[self.comp], '#include "board.h"', "#include \"hal_config.h\"", ""]
        for cid, drv, t, init, ram, extra in insts:
            c += ram + extra + [f"const {t} {cid} = {init};", ""]
        c += ["void board_init(void)", "{"]
        if self.device.upper() == "PIC24FJ256GB110":
            from .. import board as boardmod
            self.clock_lines = list(boardmod.get_profile("frcpll").clock_code)
        c += [f"    {x}" for x in self.clock_lines]
        for reg, (st, cl) in sorted(self._field_regs().items()):
            w = 32 if self.comp == "xc32" else 16 if self.comp == "xc16" else 8
            fmap = {}
            if self.dev and reg in self.dev.sfrs:
                for f in self.dev.sfrs[reg].fields:
                    if f.width == 1 and f.lsb not in fmap:
                        fmap[f.lsb] = f.name
            if all(b in fmap for b in range(w) if (st | cl) >> b & 1):
                # one named bit per line: readable, and every write is checked against the EDC field list
                for b in range(w):
                    if (st | cl) >> b & 1:
                        c.append(f"    {reg}bits.{fmap[b]} = {1 if st >> b & 1 else 0}U;")
                continue
            if cl:
                c.append(f"    {reg} &= (hal_reg_t)0x{((1 << w) - 1) ^ cl:0{w // 4}X}U;   /* clear bits {self._bits(cl)} */")
            if st:
                c.append(f"    {reg} |= (hal_reg_t)0x{st:0{w // 4}X}U;   /* set bits {self._bits(st)} */")
        pin_in, pin_out = getattr(self, "pps", ([], []))
        if pin_in or pin_out:
            c += ["", "    /* Peripheral Pin Select (unlock IOLOCK, map, lock). */",
                  "    __builtin_write_OSCCONL(OSCCON & 0xBF);", *pin_in, *pin_out, "    __builtin_write_OSCCONL(OSCCON | 0x40);"]
        c.append("}")
        self.files["board/board.c"] = H.src("board.c", "Board pin map, component instances and board_init().", "\n".join(c), dn)

    @staticmethod
    def _bits(mask):
        return ", ".join(str(i) for i in range(32) if mask >> i & 1)

    def used_drivers(self):
        out, todo = [], [b["drv"] for b in self.binds if b["drv"] in DRIVERS]
        while todo:
            d = todo.pop(0)
            if d in out:
                continue
            out.append(d)
            h, c = DRIVERS[d][1], DRIVERS[d][2]
            for m in re.findall(r'#include "(\w+)\.h"', h + c):
                if m in DRIVERS and m not in out:
                    todo.append(m)
                    self.mods |= set(DRIVERS[m][0])
        return out

    def drivers(self):
        for d in self.used_drivers():
            needs, h, c = DRIVERS[d]
            self.files[f"drivers/{d}.h"] = H.header(f"{d}.h", f"{d} driver.", h, self.device)
            self.files[f"drivers/{d}.c"] = H.src(f"{d}.c", f"{d} driver.", c, self.device)

    # ------------------------------------------------------------------ config / main
    def config(self):
        if self.device.upper() == "PIC24FJ256GB110":
            from .. import board as boardmod
            prag = boardmod.render_pragmas(boardmod.get_profile("frcpll"), self.ics)
        elif self.dev:
            prag = pragmas.render(self.dev, self.ics, self.cfg_over)
        else:
            prag = "/* no EDC data for this device - set configuration bits in MPLAB X */"
        self.files["config/config_bits.c"] = H.src("config_bits.c", "Configuration bits (" + self.clock_desc + ").",
                                                   B.INC[self.comp] + "\n\n" + prag, self.device)
        fosc_line = (f"#define _XTAL_FREQ          ({int(self.fosc)}UL)   /**< Fosc for __delay_ms() */" if self.comp == "xc8" else
                     f"#define FCY                 ({int(self.fcy)}UL)   /**< for libpic30 __delay_ms() */" if self.comp == "xc16" else "")
        body = "\n".join(['#include "hal.h"', "", f"/* Clock: {self.clock_desc}. */", fosc_line,
                          f"#define HAL_FOSC_HZ         ({int(self.fosc)}UL)", "", *self.cfg])
        self.files["hal/hal_config.h"] = H.header("hal_config.h", "Every timing constant (computed by the ChipLlama solvers).", body, self.device)

    def main(self):
        from .appgen import render_main
        self.files["app/main.c"] = H.src("main.c", "Demo application generated from the circuit.", render_main(self), self.device)

    # ------------------------------------------------------------------ build files
    def build_files(self):
        from .buildfiles import makefile, nbproject, readme
        self.files["Makefile"] = makefile(self)
        self.files.update(nbproject(self))
        self.files["README.md"] = readme(self)

    def amalgamate(self) -> str:
        """Single translation unit for the checker: headers inlined once, sources in link order."""
        hdrs = {Path(p).name: t for p, t in self.files.items() if p.endswith(".h")}
        seen: set = set()

        def expand(text):
            out = []
            for line in text.splitlines():
                m = re.match(r'\s*#include "([\w.]+)"', line)
                if m and m.group(1) in hdrs:
                    if m.group(1) not in seen:
                        seen.add(m.group(1))
                        out.append(expand(hdrs[m.group(1)]))
                    continue
                out.append(line)
            return "\n".join(out)

        order = ["config/config_bits.c", "board/board.c"] + sorted(p for p in self.files if p.startswith("hal/") and p.endswith(".c")) \
            + sorted(p for p in self.files if p.startswith("drivers/") and p.endswith(".c")) + ["app/main.c"]
        return "\n\n".join(f"/* ===== {p} ===== */\n" + expand(self.files[p]) for p in order if p in self.files)

    def verify(self):
        from .verify import verify
        return verify(self)

    def run(self) -> dict:
        self.collect()
        self.used_drivers()
        self.hal()
        self.drivers()
        self.board()
        self.config()
        self.main()
        self.build_files()
        amalg = self.amalgamate()
        ver = self.verify_safe(amalg)
        return {"device": self.device, "compiler": self.comp, "backend": self.kind, "clock": self.clock_desc,
                "files": self.files, "tree": tree(self.files), "erc": self.res["findings"], "erc_ok": self.res["ok"],
                "verified": ver["verified"], "check": ver, "notes": list(dict.fromkeys(self.notes)), "amalg": amalg,
                "name": project_name(self.d)}

    def verify_safe(self, amalg):
        self.amalg = amalg
        try:
            return self.verify()
        except Exception as e:      # never let the checker crash generation
            return {"verified": False, "findings": [{"severity": "error", "code": "CHECKER_CRASH", "message": repr(e)}]}


def project_name(d):
    n = re.sub(r"[^A-Za-z0-9_]+", "_", str(d.get("name") or "chipllama_circuit")).strip("_")
    return n or "chipllama_circuit"


def tree(files: dict) -> list[dict]:
    out = []
    for p in sorted(files):
        out.append({"path": p, "lines": files[p].count("\n") + 1, "bytes": len(files[p].encode())})
    return out


def build(d: dict, ics: str = "PGx2", packs: str = "", mode: str | None = None) -> dict:
    from .. import families as FAM
    if FAM.is_family(d.get("device", "")):
        from ..families.project import build as fbuild
        return fbuild(d, ics, packs, mode or d.get("gen_mode"))
    return Project(d, ics, packs).run()


def zip_bytes(res: dict) -> bytes:
    buf = io.BytesIO()
    root = res.get("name", "chipllama_circuit") + ("" if res.get("family") else ".X")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p, t in sorted(res["files"].items()):
            z.writestr(f"{root}/{p}", t)
    return buf.getvalue()
