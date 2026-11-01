"""Blocks for the MCU families (M8).

A family Blocks workspace names a board and a Circuit (example name or inline JSON). The Circuit gives the
deterministic HAL + drivers (same generator as the Circuit tab); the blocks become app/main.c on top of the
driver API (led_on, button_pressed, servo_set, pot_read_permille, tmp102_read, uart_dev_print ...).
VERIFIED = family checker clean on the HAL + the whole project compiles with the box/PC toolchain.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..blocks import Gen, BlocksError, Program, _cstr

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
KIND = {"led_t": "led", "button_t": "button", "servo_t": "servo", "pot_t": "pot", "tmp102_t": "temp",
        "uart_dev_t": "uart", "lm35_t": "lm35", "ntc_t": "ntc", "buzzer_t": "buzzer", "relay_t": "relay"}


def load_circuit(ref) -> dict:
    if isinstance(ref, dict):
        return ref
    name = str(ref or "")
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise BlocksError("family Blocks need a circuit (example name)")
    for f in (EXAMPLES / "circuit" / "families" / f"{name}.json", EXAMPLES / "circuit" / f"{name}.json"):
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    raise BlocksError(f"circuit {name!r} not found")


def parts_of(res: dict) -> dict:
    """{'led': ['led1'], 'button': ['sw1'], ...} from board.h externs."""
    bh = next(t for k, t in res["files"].items() if k.endswith("board.h"))
    out: dict = {}
    for typ, name in re.findall(r"extern const (\w+) (\w+);", bh):
        out.setdefault(KIND.get(typ, typ.removesuffix("_t")), []).append(name)
    return out


class FamGen(Gen):
    def __init__(self, ws, parts):
        super().__init__(ws)
        self.parts = parts
        self.helpers: set = set()

    def part(self, b, kind):
        n = str(self.f(b, "PART", "") or "")
        have = self.parts.get(kind, [])
        if n not in have:
            if not have:
                raise BlocksError(f"the circuit has no {kind}: add one on the Circuit tab")
            n = have[0]
        return n

    def uart(self):
        if not self.parts.get("uart"):
            raise BlocksError("the circuit has no UART device (USB-UART): add one on the Circuit tab")
        return self.parts["uart"][0]

    def expr(self, b, default="0"):
        if b is None:
            return default
        t = b["type"]
        if t in ("cl_button_pressed", "cl_button_read"):
            return f"({'button_pressed' if t == 'cl_button_pressed' else 'button_read'}(&{self.part(b, 'button')}) ? 1 : 0)"
        if t == "cl_pot_read":
            self.helpers.add("pot")
            return f"cl_pot(&{self.part(b, 'pot')})"
        if t == "cl_temp_read":
            self.helpers.add("temp")
            return f"cl_temp_c(&{self.part(b, 'temp')})"
        if t == "cl_millis":
            return "(int32_t)hal_millis()"
        if t in ("cl_pin_read", "cl_adc_read", "cl_spi_transfer", "cl_uart_available", "cl_uart_read"):
            raise BlocksError(f"'{t}' is a PIC register block - on {self.ws.get('device')} use the component blocks")
        return super().expr(b, default)

    def stmt(self, b, ind):
        t = b["type"]
        if t == "cl_led_set":
            v = self.f(b, "VALUE", "TOGGLE")
            fn = {"ON": "led_on", "OFF": "led_off"}.get(v, "led_toggle")
            return [f"{ind}{fn}(&{self.part(b, 'led')});"]
        if t == "cl_servo_write":
            return [f"{ind}(void)servo_set(&{self.part(b, 'servo')}, (uint16_t)({self.expr(self.inp(b, 'DEG'), '90')}));"]
        if t == "cl_delay_ms":
            return [f"{ind}hal_delay_ms({int(self.f(b, 'MS', 100))}U);"]
        if t == "cl_delay_us":
            return [f"{ind}hal_delay_us({int(self.f(b, 'US', 10))}U);"]
        if t in ("cl_uart_print", "cl_uart_println"):
            nl = "\\r\\n" if t == "cl_uart_println" or self.f(b, "NL") == "TRUE" else ""
            return [f"{ind}(void)uart_dev_print(&{self.uart()}, {_cstr(str(self.f(b, 'TEXT', '')))[:-1]}{nl}\");"]
        if t == "cl_uart_print_num":
            u = self.uart()
            v = self.expr(self.inp(b, "VALUE"))
            if self.f(b, "NL", "TRUE") == "TRUE":
                return [f"{ind}(void)uart_dev_println_int(&{u}, (int32_t)({v}));"]
            return [f"{ind}(void)hal_uart_write_int({u}.uart, (int32_t)({v}));"]
        if t in ("cl_uart_begin", "cl_i2c_begin", "cl_spi_begin", "cl_pin_mode"):
            self.p.warnings.append(f"'{t}' is not needed on a family board: the Circuit already configures it")
            return []
        if t in ("cl_pin_write", "cl_pin_toggle", "cl_spi_send", "cl_i2c_write"):
            raise BlocksError(f"'{t}' is a PIC register block - on {self.ws.get('device')} use the component blocks (LED, servo ...)")
        return super().stmt(b, ind)

    def run(self) -> Program:
        p = self.p
        blocks = (self.ws.get("blocks") or {}).get("blocks", [])
        for b in blocks:
            t = b["type"]
            if t == "cl_on_start":
                p.start += self.stmts(self.inp(b, "DO"), "    ")
            elif t == "cl_forever":
                p.loop += self.stmts(self.inp(b, "DO"), "        ")
            elif t == "cl_every":
                if p.every:
                    raise BlocksError("only one 'every' block")
                ms = float(self.f(b, "MS", 500))
                if not 1 <= ms <= 3600000:
                    raise BlocksError("'every' period must be 1 ms .. 1 h (millisecond tick)")
                p.every = (ms, self.stmts(self.inp(b, "DO"), "            "))
            elif t in ("cl_on_pin_change", "cl_on_uart_rx"):
                raise BlocksError(f"'{t}' is PIC-only for now; poll 'button pressed' inside 'forever' instead")
            else:
                p.warnings.append(f"loose block '{t}' outside an event is ignored")
        if not any(b.get("type") in ("cl_on_start", "cl_forever", "cl_every") for b in blocks):
            raise BlocksError("add an event block (on start, forever, every ...) to begin")
        return p


def _main(res, prog: Program, g: FamGen, entry: str) -> str:
    key = next(k for k in res["files"] if k.endswith(("app/main.c", "src/main.c")))
    old = res["files"][key]
    incs = [ln for ln in old.splitlines() if ln.startswith("#include")]
    if g.parts.get("uart") and '#include "hal_uart.h"' not in incs:
        incs.append('#include "hal_uart.h"')
    body = old[old.index(f"int {entry}(void)"):]
    init = [ln for ln in body[body.index("    board_init();"):body.index("    for (;;)")].rstrip().splitlines()]
    hl = []
    if "pot" in g.helpers:
        hl += ["/** @brief Potentiometer position 0..1000 (per mille). */", "static int32_t cl_pot(const pot_t *dev)", "{",
               "    uint16_t v = 0U;", "    (void)pot_read_permille(dev, &v);", "    return (int32_t)v;", "}", ""]
    if "temp" in g.helpers:
        hl += ["/** @brief TMP102 temperature in whole degC. */", "static int32_t cl_temp_c(const tmp102_t *dev)", "{",
               "    int16_t t = 0;", "    (void)tmp102_read(dev, &t);", "    return (int32_t)(t / 16);", "}", ""]
    L = ["/**", " * @file    main.c", " * @brief   Application generated from the Blocks workspace.", " *",
         f" * Generated by ChipLlama Blocks (deterministic, no AI) for {res['device']}.", " */", *incs, ""]
    L += hl
    L += [f"int {entry}(void)", "{"]
    vars_ = sorted(prog.vars)
    for v in vars_:
        L.append(f"    int32_t {v} = 0;")
    if prog.every:
        L.append("    uint32_t cl_last_every = 0UL;")
    L += init + [""] + prog.start + ["", "    for (;;)", "    {"]
    if prog.every:
        L += [f"        if ((hal_millis() - cl_last_every) >= {int(prog.every[0])}UL)", "        {",
              f"            cl_last_every += {int(prog.every[0])}UL;"] + prog.every[1] + ["        }"]
    L += prog.loop + ["    }", "}", ""]
    return key, "\n".join(L)


def generate(ws: dict, settings=None, compile: bool = False) -> dict:
    from .. import projgen
    from . import toolchain as TC
    out = {"c": "", "intent": "", "warnings": [], "verified": False, "findings": [], "error": "", "family": True}
    try:
        d = load_circuit(ws.get("circuit"))
        if ws.get("device") and ws["device"] != d.get("device"):
            raise BlocksError(f"workspace board {ws['device']} differs from the circuit's {d.get('device')}")
        mode = ws.get("gen_mode") or getattr(settings, "family_mode", None)
        res = projgen.build(d, mode=mode if res_family(d) == "avr" else None)
        if not res.get("erc_ok"):
            raise BlocksError("the circuit has ERC errors")
        g = FamGen(ws, parts_of(res))
        prog = g.run()
        key, code = _main(res, prog, g, "cl_app_main" if res["mode"] == "arduino" else "main")
    except BlocksError as e:
        out["error"] = str(e)
        return out
    res["files"][key] = code
    out.update(c=code, warnings=prog.warnings, device=res["device"], mode=res["mode"], main=key,
               intent=f"{res['device']} ({res['mode']}): " + ", ".join(f"{k}: {', '.join(v)}" for k, v in g.parts.items()),
               findings=[f for f in res["check"]["findings"]], parts=g.parts)
    out["verified"] = bool(res["verified"])
    if compile:
        cr = TC.compile_result(res)
        cr.pop("hex", None)
        out["compile"] = cr
        if cr["ok"] is False:
            out["verified"] = False
            out["findings"].append({"severity": "error", "code": "COMPILE", "message": cr["log"][-1200:]})
    out["project"] = res
    return out


def res_family(d):
    from . import family_of
    return family_of(d.get("device"))
