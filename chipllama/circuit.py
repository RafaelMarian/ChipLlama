"""Circuit tab backend: netlist, MCU pin-function assignment, electrical rule checks (ERC) and bindings.

Diagram JSON::

    {"device": "PIC24FJ256GB110",
     "parts": [{"id": "U1", "type": "mcu"}, {"id": "LED1", "type": "led", "params": {...}, "x": 0, "y": 0}],
     "wires": [{"a": "U1.72", "b": "R1.1"}],          # endpoint = <part id>.<port>; MCU port = pin number
     "assign": {"72": "GPIO_OUT"},                      # optional per-pin function override
     "power": "board"}                                  # "board": VDD/VSS/VCAP wired by the dev board, "manual": check them

Every electrical limit carries its DS39897C (PIC24FJ256GB110 family) page.  For other devices the same
rules run with the GB110 numbers flagged as "typical PIC24/18 value - check your datasheet".
"""
from __future__ import annotations

import re
from collections import defaultdict

from . import components as C
from . import pinout as PO

PIN_ABS_MAX_MA = 25.0      # p.311 absolute maximum: max current sunk/sourced by any I/O pin
VOH_SPEC_MA = 6.0          # p.319 Table 29-8 DO20: VOH >= 2.4 V at IOH = -6 mA (VDD 3.6 V)
VOL_SPEC_MA = 8.5          # p.319 DO10: VOL <= 0.4 V at IOL 8.5 mA
VSS_MAX_MA = 300.0         # p.311 max current out of VSS
VDD_MAX_MA = 250.0         # p.311 max current into VDD
VCAP_UF = 10.0             # p.29 / p.293: 10 uF, ESR < 5 ohm, >= 6.3 V, tantalum or ceramic; never to VDD
VBE = 0.7


class Netlist:
    def __init__(self, wires):
        self.parent: dict[str, str] = {}
        for w in wires:
            self.union(w["a"], w["b"])

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)

    def nets(self):
        g = defaultdict(set)
        for x in list(self.parent):
            g[self.find(x)].add(x)
        return g


class Circuit:
    def __init__(self, d: dict, packs: str = ""):
        self.d = d
        self.device = (d.get("device") or "PIC24FJ256GB110").upper()
        self.info = PO.get(self.device, packs)
        self.pins = {str(p["num"]): p for p in self.info["pins"]}
        self.parts = {p["id"]: p for p in d.get("parts", [])}
        self.mcu = next((p["id"] for p in d.get("parts", []) if p.get("type") == "mcu"), "U1")
        self.nl = Netlist([w for w in d.get("wires", []) if w.get("a") and w.get("b")])
        groups = self.nl.nets()
        self.net_of: dict[str, frozenset] = {}
        for members in groups.values():
            fs = frozenset(members)
            for m in members:
                self.net_of[m] = fs
        self.findings: list[dict] = []
        self.assign: dict[str, str] = {}
        self.bindings: list[dict] = []

    # ------------------------------------------------------------ helpers
    def add(self, sev, code, msg, parts=(), pins=(), page=None):
        self.findings.append({"severity": sev, "code": code, "message": msg, "parts": list(parts),
                              "pins": [str(p) for p in pins], "page": page})

    def net(self, ep: str) -> frozenset:
        return self.net_of.get(ep, frozenset([ep]))

    def ptype(self, pid):
        return self.parts.get(pid, {}).get("type")

    def part(self, pid) -> C.Part | None:
        return C.LIB.get(self.ptype(pid))

    def param(self, pid, k, default=None):
        p = self.parts.get(pid, {})
        v = (p.get("params") or {}).get(k)
        if v is None:
            lib = self.part(pid)
            v = lib.params.get(k, default) if lib else default
        return v

    def mcu_pins(self, net) -> list[str]:
        return [ep.split(".", 1)[1] for ep in net if ep.split(".", 1)[0] == self.mcu]

    def others(self, net, exclude_pid=None):
        return [tuple(ep.split(".", 1)) for ep in net if ep.split(".", 1)[0] not in (self.mcu, exclude_pid)]

    def rail(self, net) -> float | None:
        """Voltage of a net when it is tied to a supply symbol (None = signal net)."""
        for pid, port in self.others(net):
            t = self.ptype(pid)
            if t in ("vdd33", "v5"):
                return float(self.param(pid, "v", 3.3 if t == "vdd33" else 5.0))
            if t == "gnd" or (t == "battery" and port == "-") or (t == "regulator" and port == "GND"):
                return 0.0
            if t == "battery" and port == "+":
                return float(self.param(pid, "v", 3.7))
            if t == "regulator" and port == "VOUT":
                return float(self.param(pid, "vout", 3.3))
        for pin in self.mcu_pins(net):
            pw = self.pins.get(pin, {}).get("power")
            if pw in ("vdd",):
                return 3.3
            if pw == "vss":
                return 0.0
        return None

    def resistors_to(self, net):
        """[(resistor id, other-end net)] for resistors with one end on net."""
        out = []
        for pid, port in self.others(net):
            if self.ptype(pid) == "resistor":
                other = f"{pid}.{'2' if port == '1' else '1'}"
                out.append((pid, self.net(other)))
        return out

    def ohms(self, pid) -> float:
        try:
            return float(self.param(pid, "ohms", 330))
        except (TypeError, ValueError):
            return 330.0

    def has_pullup(self, net) -> bool:
        return any((self.rail(n) or 0) > 0 for _, n in self.resistors_to(net))

    # ------------------------------------------------------------ pin functions
    def valid_functions(self, num: str) -> list[str]:
        p = self.pins.get(str(num))
        if not p or p["power"] or not p["port"]:
            return []
        up = [n.upper() for n in p["names"]]
        out = ["GPIO_OUT", "GPIO_IN", "GPIO_IO"]
        if p["an"] is not None:
            out.append(f"AN{p['an']}")
        fixed = [n for n in up if re.fullmatch(r"(A?SCL|A?SDA|SCK|SDI|SDO|TX|RX|U\dTX|U\dRX|CCP|P\d[A-D]|OC|PWM\d[HL]?)\d*", n)]
        out += [re.sub(r"^A(SCL|SDA)", r"\1", n) for n in fixed]
        if self.info.get("pps") == "complex" and self.info["family"] == "pic24":
            rp, rpi = p["rp"], p["rpi"]
            if rp is not None or rpi is not None:
                out += [f"U{n}RX" for n in (1, 2, 3, 4)] + [f"SDI{n}" for n in (1, 2, 3)]
            if rp is not None:
                out += [f"U{n}TX" for n in (1, 2, 3, 4)] + [f"OC{n}" for n in range(1, 10)] + \
                       [f"SCK{n}OUT" for n in (1, 2, 3)] + [f"SDO{n}" for n in (1, 2, 3)]
        if "SOSCI" in up or "SOSCO" in up:
            out.append("SOSCI" if "SOSCI" in up else "SOSCO")
        return list(dict.fromkeys(out))

    def pin_by_fn(self, fn: str) -> str | None:
        """Fixed-function pin (I2C, ANx, non-PPS UART/CCP) for a function name."""
        for num, p in self.pins.items():
            if fn in [n.upper() for n in p["names"]] or ("A" + fn) in [n.upper() for n in p["names"]] and False:
                return num
        return None

    # ------------------------------------------------------------ analysis
    def drive_of(self, net, depth=0):
        """How an MCU pin reaches this net: (pin, via, ohms, transistor) or None.
        via: direct | resistor | npn | nmos | pnp | pmos"""
        pins = [x for x in self.mcu_pins(net) if not self.pins.get(x, {}).get("power")]
        if pins:
            return pins[0], "direct", 0.0, None
        for rid, other in self.resistors_to(net):
            pins = [x for x in self.mcu_pins(other) if not self.pins.get(x, {}).get("power")]
            if pins:
                return pins[0], "resistor", self.ohms(rid), rid
        if depth == 0:
            for pid, port in self.others(net):
                t = self.ptype(pid)
                if t in ("npn", "pnp") and port == "C" or t in ("nmos", "pmos") and port == "D":
                    ctl = self.net(f"{pid}.{'B' if t in ('npn', 'pnp') else 'G'}")
                    r = self.drive_of(ctl, 1)
                    if r:
                        return r[0], t, r[2], pid
                if t == "opto" and port in ("C",):
                    r = self.drive_of(self.net(f"{pid}.A"), 1)
                    if r:
                        return r[0], "opto", r[2], pid
        return None

    def two_terminal(self, pid, a, k):
        """Bind a passive 2-terminal load (LED, buzzer, coil, motor, button): find which side the MCU drives."""
        for side, other, high in ((a, k, True), (k, a, False)):
            d = self.drive_of(self.net(f"{pid}.{side}"))
            if d:
                pin, via, ohms, q = d
                if via in ("pnp", "pmos"):
                    high = not high
                return {"pin": pin, "via": via, "ohms": ohms, "q": q, "side": side,
                        "other_rail": self.rail(self.net(f"{pid}.{other}")), "active_high": high if via == "direct" or via == "resistor" else via in ("npn", "nmos", "opto")}
        return None

    def run(self) -> dict:
        if not self.info or self.info.get("generic"):
            self.add("warning", "DEVICE_GENERIC", f"No pin table for {self.device}: showing a generic block. Install its DFP "
                     "(MPLAB X packs) so the real pinout is read from edc/<device>.PIC.")
        used_fn: dict[str, str] = {}
        counters = defaultdict(int)
        want: dict[str, tuple[str, str]] = {}      # pin -> (needed role, part id)

        def need(pin, role, pid, port):
            if pin in want and want[pin][0] != role and not {want[pin][0], role} <= {"in", "io"}:
                self.add("error", "PIN_SHARED", f"pin {self.label(pin)} is wired to {want[pin][1]} ({want[pin][0]}) and "
                         f"{pid}.{port} ({role})", [want[pin][1], pid], [pin])
            want.setdefault(pin, (role, pid))

        # ---- bind each part
        uart_n = 0
        for pid, pd in self.parts.items():
            t = pd.get("type")
            lib = C.LIB.get(t)
            if t == "mcu" or not lib:
                continue
            b = {"id": pid, "type": t, "drv": lib.drv, "params": {k: self.param(pid, k) for k in lib.params}, "pins": {}}
            if t in ("led", "buzzer_active", "relay", "dc_motor"):
                a, k = {"led": ("A", "K"), "relay": ("COIL+", "COIL-")}.get(t, ("+", "-"))
                r = self.two_terminal(pid, a, k)
                if r:
                    b["pins"]["CTL"] = dict(r, role="out")
                    need(r["pin"], "out", pid, a)
            elif t == "rgb_led":
                for col in "RGB":
                    r = self.two_terminal(pid, col, "K")
                    if r:
                        b["pins"][col] = dict(r, role="out")
                        need(r["pin"], "out", pid, col)
            elif t in ("button", "switch"):
                r = self.two_terminal(pid, "1", "2")
                if r:
                    b["pins"]["IN"] = dict(r, role="in", active_high=(r["other_rail"] or 0) > 0)
                    need(r["pin"], "in", pid, r["side"])
            for port, role in lib.ports:
                if role in ("p", "vcc", "gnd"):
                    continue
                pins = [x for x in self.mcu_pins(self.net(f"{pid}.{port}")) if not self.pins.get(x, {}).get("power")]
                if not pins and role == "an" and t in ("ldr", "ntc"):
                    pass
                if pins:
                    b["pins"][port] = {"pin": pins[0], "role": role, "active_high": True}
                    need(pins[0], role, pid, port)
            if lib.drv == "uart_dev" and b["pins"]:
                uart_n += 1
                b["uart"] = uart_n
            self.bindings.append(b)

        # ---- choose / validate a function for every used MCU pin
        user = {str(k): str(v).upper() for k, v in (self.d.get("assign") or {}).items()}
        uart_of = {}
        for b in self.bindings:
            for port, pb in b["pins"].items():
                pin = pb["pin"]
                fn = user.get(pin) or self.auto_fn(pin, pb["role"], b, counters)
                valid = self.valid_functions(pin)
                if fn not in valid and not (fn == "SOSCI" and pb["role"] == "p"):
                    self.add("error", "PIN_FN_INVALID", f"{b['id']}.{port} needs {C.ROLE_FN.get(pb['role'], pb['role'])} but "
                             f"pin {self.label(pin)} cannot do {fn}. Valid here: {', '.join(valid[:12]) or 'nothing (power pin)'}",
                             [b["id"]], [pin], 136 if self.verified else None)
                if pin in used_fn and used_fn[pin] != fn:
                    self.add("error", "PIN_CONFLICT", f"pin {self.label(pin)} is assigned both {used_fn[pin]} and {fn}",
                             [b["id"]], [pin])
                used_fn[pin] = fn
                pb["fn"] = fn
                pb["label"] = self.label(pin)
                p = self.pins.get(pin, {})
                pb.update(rp=p.get("rp") if p.get("rp") is not None else p.get("rpi"), an=p.get("an"), cn=p.get("cn"),
                          port_name=p.get("port"), tol5v=p.get("tol5v"))
        self.assign = used_fn
        self.erc()
        if self.verified and self.info.get("pps"):
            self.plan_check()
        return self.result()

    @property
    def verified(self):
        return self.device == "PIC24FJ256GB110"

    def label(self, pin):
        p = self.pins.get(str(pin))
        return f"{p['label']} (pin {pin})" if p else f"pin {pin}"

    def auto_fn(self, pin, role, b, counters):
        valid = self.valid_functions(pin)
        p = self.pins.get(pin, {})
        if role in ("out", "cs"):
            return "GPIO_OUT"
        if role == "in":
            return "GPIO_IN"
        if role == "io":
            return "GPIO_IO"
        if role == "an":
            return f"AN{p.get('an')}" if p.get("an") is not None else "AN?"
        if role in ("scl", "sda"):
            return next((f for f in valid if f.startswith(role.upper())), role.upper() + "?")
        if role == "pwm":
            have = [f for f in valid if re.fullmatch(r"OC\d|CCP\d|PWM\d[HL]?", f)]
            if self.info["pps"] == "complex" and self.info["family"] == "pic24":
                counters["oc"] += 1
                return f"OC{counters['oc']}"
            return have[0] if have else "OC?"
        if role in ("rx", "tx"):      # device RX <- MCU TX
            n = b.get("uart", 1)
            want = f"U{n}TX" if role == "rx" else f"U{n}RX"
            if want in valid:
                return want
            fixed = [f for f in valid if re.fullmatch(r"(TX|RX)\d?", f) and f.startswith("TX" if role == "rx" else "RX")]
            return fixed[0] if fixed else want
        if role in ("sck", "mosi", "miso"):
            fam = {"sck": ("SCK1OUT", "SCK1"), "mosi": ("SDO1",), "miso": ("SDI1",)}[role]
            return next((f for f in fam if f in valid), next((f for f in valid if f.startswith(fam[0][:3])), fam[0]))
        return "GPIO_IO"

    # ------------------------------------------------------------ ERC
    def erc(self):
        pg = (lambda n: n) if self.verified else (lambda n: None)
        note = "" if self.verified else " (PIC24FJ256GB110 figure; check your device's DC characteristics)"
        total_ma = 0.0
        for b in self.bindings:
            pid, t = b["id"], b["type"]
            ctl = b["pins"].get("CTL")
            if t in ("led", "rgb_led"):
                for col, pb in (b["pins"].items() if t == "rgb_led" else [("CTL", ctl)] if ctl else []):
                    vf = float(self.param(pid, "vf", 2.0) if t == "led" else 2.5)
                    if pb["via"] == "direct":
                        self.add("error", "LED_NO_R", f"{pid}: LED on {self.label(pb['pin'])} has no series resistor - the pin "
                                 f"would source/sink far more than {PIN_ABS_MAX_MA:g} mA (absolute maximum, p.311){note}. "
                                 f"Add ~{max(68, round((3.3 - vf) / 0.005, -1)):.0f} ohm for 5 mA.", [pid], [pb["pin"]], pg(311))
                        continue
                    if pb["via"] == "resistor":
                        i = max(0.0, (3.3 - vf) / max(pb["ohms"], 1) * 1000)
                        total_ma += i
                        b.setdefault("current_ma", round(i, 2))
                        if i > PIN_ABS_MAX_MA:
                            self.add("error", "LED_CURRENT", f"{pid}: {i:.1f} mA through {self.label(pb['pin'])} exceeds the "
                                     f"{PIN_ABS_MAX_MA:g} mA per-pin absolute maximum (p.311){note}. Raise R or drive it with "
                                     "an NPN / N-MOSFET.", [pid], [pb["pin"]], pg(311))
                        elif i > VOH_SPEC_MA:
                            self.add("warning", "LED_CURRENT", f"{pid}: {i:.1f} mA is above the {VOH_SPEC_MA:g} mA the output is "
                                     f"specified at (VOH 2.4 V @ 6 mA, VOL 0.4 V @ 8.5 mA, p.319){note}; the LED will be dimmer "
                                     "than calculated. Use >= "
                                     f"{round((3.3 - vf) / VOH_SPEC_MA * 1000 + 5, -1):.0f} ohm or a transistor.",
                                     [pid], [pb["pin"]], pg(319))
                        else:
                            self.add("info", "LED_OK", f"{pid}: ~{i:.1f} mA from {self.label(pb['pin'])} (Vf {vf:g} V, "
                                     f"R {pb['ohms']:g} ohm) - within the 6 mA output spec (p.319).", [pid], [pb["pin"]], pg(319))
            if t in ("relay", "dc_motor") and ctl:
                if ctl["via"] in ("direct", "resistor"):
                    self.add("error", "INDUCTIVE_DIRECT", f"{pid}: a {'relay coil' if t == 'relay' else 'motor'} cannot be driven "
                             f"from {self.label(ctl['pin'])} ({PIN_ABS_MAX_MA:g} mA max, p.311). Use an NPN/N-MOSFET low-side "
                             "switch with a flyback diode.", [pid], [ctl["pin"]], pg(311))
                a, k = ("COIL+", "COIL-") if t == "relay" else ("+", "-")
                na, nk = self.net(f"{pid}.{a}"), self.net(f"{pid}.{k}")
                ok = any(self.ptype(x) == "diode" and f"{x}.K" in na and f"{x}.A" in nk for x in self.parts)
                if not ok:
                    self.add("error", "NO_FLYBACK", f"{pid}: no flyback diode across the {'coil' if t == 'relay' else 'motor'} "
                             f"(cathode to {a}, anode to {k}). The turn-off spike will kill the transistor/pin.", [pid])
            if t == "relay" and not ctl:
                self.add("warning", "UNDRIVEN", f"{pid}: coil is not driven by any pin (through a transistor).", [pid])
            if t in ("button", "switch"):
                pb = b["pins"].get("IN")
                if not pb:
                    self.add("warning", "UNDRIVEN", f"{pid}: not connected to an MCU pin.", [pid])
                    continue
                net = self.net(f"{pid}.{pb['side']}")
                if pb["active_high"]:
                    if not any(self.rail(n) == 0 for _, n in self.resistors_to(net)):
                        self.add("warning", "BTN_PULLDOWN", f"{pid} switches to VDD: add a 10 k pull-down on "
                                 f"{self.label(pb['pin'])} or it floats when released.", [pid], [pb["pin"]])
                elif not self.has_pullup(net):
                    if self.param(pid, "pullup", "internal") == "internal":
                        p = self.pins.get(pb["pin"], {})
                        if p.get("cn"):
                            self.add("info", "BTN_PULLUP", f"{pid}: using the internal weak pull-up {p['cn']}PUE on "
                                     f"{self.label(pb['pin'])} (50-400 uA, p.318).", [pid], [pb["pin"]], pg(318))
                        elif self.info["family"] in ("pic24", "dspic33") or not self.weak_pullup_ok(pb["pin"]):
                            self.add("warning", "BTN_PULLUP", f"{pid}: {self.label(pb['pin'])} has no internal pull-up; add a 10 k "
                                     "resistor to VDD.", [pid], [pb["pin"]])
                    else:
                        self.add("warning", "BTN_PULLUP", f"{pid}: no pull-up on {self.label(pb['pin'])} - the input floats when "
                                 "the button is open. Add 10 k to VDD or set pull-up = internal (CNxPUE, p.135).",
                                 [pid], [pb["pin"]], pg(135))
            for port, pb in b["pins"].items():
                if pb["role"] in ("scl", "sda"):
                    if not self.has_pullup(self.net(f"{pid}.{port}")) and not self.d.get("module_pullups", {}).get(pid):
                        self.add("warning", "I2C_PULLUP", f"{pid}.{port} ({self.label(pb['pin'])}): I2C needs a pull-up "
                                 "(2.2-4.7 k to 3.3 V). Breakout boards often have one - tick 'module has pull-ups' if so.",
                                 [pid], [pb["pin"]], pg(191))
            for port, role in (C.LIB[t].ports if t in C.LIB else []):
                if role == "vcc":
                    v = self.rail(self.net(f"{pid}.{port}"))
                    if v is None:
                        self.add("warning", "NO_SUPPLY", f"{pid}.{port} (supply) is not connected to a rail.", [pid])
                    elif t in ("sdcard", "esp01", "ssd1306", "bme280", "mpu6050", "tmp102") and v > 3.6:
                        self.add("error", "OVERVOLT", f"{pid} is a 3.3 V part but {port} is on {v:g} V (check the module has a "
                                 "regulator).", [pid])
                    elif t == "lm35" and v < 4:
                        self.add("warning", "LM35_VS", f"{pid}: LM35 needs VS >= 4 V; it is on {v:g} V.", [pid])
                    elif t == "servo" and v < 4.5:
                        self.add("warning", "SERVO_SUPPLY", f"{pid}: hobby servos want 4.8-6 V on V+ (on {v:g} V).", [pid])
            # 5 V on a non-tolerant pin
            vio = self.module_voltage(pid)
            if vio and vio > 3.6:
                for port, pb in b["pins"].items():
                    if pb["role"] in ("tx", "out", "io", "miso", "sda", "scl") or (pb["role"] == "in" and t in ("mcp2515",)):
                        drives = pb["role"] in ("tx", "io", "miso", "sda", "scl") or t == "mcp2515"
                        if not drives:
                            continue
                        tol = self.pins.get(pb["pin"], {}).get("tol5v")
                        if tol is False:
                            self.add("error", "NOT_5V_TOLERANT", f"{pid}.{port} drives {vio:g} V into {self.label(pb['pin'])}, "
                                     "which is not 5 V tolerant (analog-capable pin, Table 10-1 p.134). Move it to a 5.5 V "
                                     "tolerant digital pin or add a divider/level shifter.", [pid], [pb["pin"]], 134)
                        elif tol is None:
                            self.add("warning", "NOT_5V_TOLERANT", f"{pid}.{port} drives {vio:g} V into {self.label(pb['pin'])}: "
                                     "5 V tolerance unknown for this device - check the datasheet I/O table.", [pid], [pb["pin"]])
        # 5 V rail straight on a pin
        for num in self.pins:
            if self.pins[num]["power"]:
                continue
            net = self.net(f"{self.mcu}.{num}")
            v = self.rail(net)
            if v and v > 3.6 and len(net) > 1:
                tol = self.pins[num]["tol5v"]
                self.add("error" if tol is False else "warning", "NOT_5V_TOLERANT",
                         f"{self.label(num)} is tied to the {v:g} V rail" + (" and is not 5 V tolerant (p.134)." if tol is False else
                         " - only digital-only pins tolerate 5.5 V on input (p.134)."), [], [num], 134 if self.verified else None)
            if v is not None and len(net) > 1 and num in self.assign and self.assign[num].startswith("GPIO_OUT"):
                self.add("error", "OUTPUT_SHORT", f"{self.label(num)} is an output wired straight to a rail.", [], [num])
        # crystal
        for pid in self.parts:
            if self.ptype(pid) == "crystal":
                pins = [x for port in ("1", "2") for x in self.mcu_pins(self.net(f"{pid}.{port}"))]
                names = {n.upper() for x in pins for n in self.pins.get(x, {}).get("names", [])}
                if not pins:
                    self.add("warning", "XTAL_OPEN", f"{pid}: connect the crystal to SOSCI/SOSCO.", [pid])
                elif not {"SOSCI", "SOSCO"} <= names:
                    self.add("error", "XTAL_PINS", f"{pid}: a 32.768 kHz crystal must sit on SOSCI/SOSCO "
                             + ("(pins 73/74, p.123)." if self.verified else "."), [pid], pins, pg(123))
                else:
                    for x in pins:
                        self.assign[x] = "SOSCI" if "SOSCI" in [n.upper() for n in self.pins[x]["names"]] else "SOSCO"
                    self.add("info", "XTAL_OK", f"{pid}: SOSC crystal - add 2 x 12-22 pF to GND and set SOSCSEL/RTCC "
                             "clock in config bits.", [pid], pins)
        # floating inputs: pin assigned input with nothing else on its net
        for b in self.bindings:
            for port, pb in b["pins"].items():
                if pb["role"] in ("an",) and len(self.net(f"{b['id']}.{port}")) < 2:
                    self.add("warning", "FLOATING", f"{b['id']}.{port} is not wired.", [b["id"]])
        for pid, pd in self.parts.items():
            lib = C.LIB.get(pd.get("type"))
            if not lib:
                continue
            for port, role in lib.ports:
                if f"{pid}.{port}" not in self.net_of and role not in ("p",):
                    self.add("info", "UNCONNECTED", f"{pid}.{port} is not connected.", [pid])
        # power pins
        if total_ma > 0:
            self.add("info", "IO_BUDGET", f"I/O current budget: ~{total_ma:.1f} mA from LEDs (VDD max {VDD_MAX_MA:g} mA, "
                     f"VSS max {VSS_MAX_MA:g} mA, p.311).", [], [], pg(311))
        if self.verified:
            if (self.d.get("power") or "board") == "board":
                self.add("info", "POWER_BOARD", "Supply assumed from the dev board: all VDD (2,16,37,46,62) + AVDD 30 to 3.3 V, all "
                         "VSS (15,36,45,65,75) + AVSS 31 to GND with 0.1 uF each (p.27); VCAP pin 85 -> 10 uF low-ESR "
                         "to GND, never to VDD (p.29); ENVREG pin 86 -> VDD to enable the core regulator (p.293); "
                         "MCLR pin 13 -> 10 k pull-up (p.28).", [], [], 29)
            else:
                self.power_manual()

    def weak_pullup_ok(self, pin):
        names = [n.upper() for n in self.pins.get(pin, {}).get("names", [])]
        return any(n.startswith(("RB", "IOC")) for n in names)

    def module_voltage(self, pid) -> float | None:
        lib = self.part(pid)
        if not lib:
            return None
        io = self.param(pid, "io_v")
        if io:
            return float(io)
        for port, role in lib.ports:
            if role == "vcc":
                v = self.rail(self.net(f"{pid}.{port}"))
                if v is not None:
                    return v
        return lib.logic_v if lib.logic_v and lib.logic_v > 3.6 else None

    def power_manual(self):
        for num, p in self.pins.items():
            pw = p["power"]
            if not pw:
                continue
            net = self.net(f"{self.mcu}.{num}")
            v = self.rail(net) if len(net) > 1 else None
            if pw == "vdd" and v != 3.3:
                self.add("error", "VDD_PIN", f"{p['label']} pin {num} must go to 3.3 V with a 0.1 uF decoupling cap (p.27).",
                         [], [num], 27)
            elif pw == "vss" and v != 0:
                self.add("error", "VSS_PIN", f"{p['label']} pin {num} must go to GND (p.27).", [], [num], 27)
            elif pw == "vcap":
                if v is not None and v > 0:
                    self.add("error", "VCAP_VDD", "VCAP/VDDCORE (pin 85) must NOT be connected to VDD (p.29).", [], [num], 29)
                caps = [x for x, port in self.others(net) if self.ptype(x) == "capacitor"]
                uf = max([float(self.param(c, "farads", 0)) * 1e6 for c in caps] or [0])
                if uf < 4.7:
                    self.add("error", "VCAP", f"VCAP (pin 85) needs a {VCAP_UF:g} uF, ESR < 5 ohm, >= 6.3 V capacitor to GND "
                             f"(p.29); found {uf:g} uF.", [], [num], 29)
            elif pw == "envreg" and v != 3.3:
                self.add("warning", "ENVREG", "ENVREG (pin 86) should be tied to VDD to enable the on-chip 2.5 V core regulator; "
                         "tied to VSS you must supply VDDCORE externally (p.293).", [], [num], 293)
            elif pw == "mclr" and not self.has_pullup(net):
                self.add("warning", "MCLR", "MCLR (pin 13) needs a 10 k pull-up to VDD (p.28).", [], [num], 28)

    def plan_check(self):
        from . import pins as pn
        items = []
        for num, fn in self.assign.items():
            port = self.pins.get(num, {}).get("port")
            if not port or fn in ("GPIO_IO", "SOSCI", "SOSCO") or fn.endswith("?"):
                continue
            items.append({"fn": fn, "pin": port})
        try:
            r = pn.plan(items, self.d.get("ics", "PGx2"))
        except Exception as e:      # pragma: no cover
            self.add("warning", "PLAN", f"pin planner: {e}")
            return
        for i in r["issues"]:
            pins = [n for n, p in self.pins.items() if p["port"] and p["port"] in i["msg"]]
            self.add(i["severity"], "PLAN", "pin planner: " + i["msg"], [], pins, 136)
        self.pins_init = r["code"]

    def result(self) -> dict:
        sev = {"error": 0, "warning": 1, "info": 2}
        self.findings.sort(key=lambda f: sev.get(f["severity"], 3))
        bad = sorted({p for f in self.findings if f["severity"] == "error" for p in f["pins"]})
        return {"device": self.device, "ok": not any(f["severity"] == "error" for f in self.findings),
                "findings": self.findings, "assign": self.assign, "bad_pins": bad, "bindings": self.bindings,
                "verified_device": self.verified}


def check(d: dict, packs: str = "") -> dict:
    return Circuit(d, packs).run()


def pin_functions(device: str, num: str, packs: str = "") -> list[str]:
    return Circuit({"device": device, "parts": [{"id": "U1", "type": "mcu"}]}, packs).valid_functions(str(num))


def meta(device: str, packs: str = "") -> dict:
    info = PO.get(device, packs)
    c = Circuit({"device": device, "parts": [{"id": "U1", "type": "mcu"}]}, packs)
    for p in info["pins"]:
        p["functions"] = c.valid_functions(str(p["num"]))
    return {"device": info, "library": C.library(), "bundled": PO.bundled()}
