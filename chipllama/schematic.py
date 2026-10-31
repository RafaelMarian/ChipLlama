"""KiCad-style schematic helpers (M7): netlist export, connection guide, auto-wire, MCU power checklist, migration."""
from __future__ import annotations

import copy
import json
import re
import time

from . import components as C
from . import symbols as S
from .circuit import Circuit, check


# ---------------------------------------------------------------- schema migration
def migrate(d: dict) -> dict:
    """Schema-1 circuits (endpoint = logical port) -> schema 2 (endpoint = physical pin number)."""
    d = copy.deepcopy(d)
    if int(d.get("schema") or 1) >= 2:
        return d
    types = {p["id"]: p.get("type") for p in d.get("parts", [])}
    for w in d.get("wires", []):
        for k in ("a", "b"):
            pid, _, port = str(w.get(k, "")).partition(".")
            t = types.get(pid)
            if t and t != "mcu":
                nums = S.pins_of_port(t, port)
                if nums:
                    w[k] = f"{pid}.{nums[0]}"
    d["schema"] = 2
    return d


# ---------------------------------------------------------------- netlist
def nets(d: dict, packs: str = "") -> list[dict]:
    c = Circuit(d, packs)
    seen, out = set(), []
    for ep, net in c.net_of.items():
        if net in seen:
            continue
        seen.add(net)
        nodes, name = [], None
        for e in sorted(net):
            pid, port = e.split(".", 1)
            t = c.ptype(pid)
            if t in C.POWER_SYM:
                name = C.POWER_SYM[t]
                continue
            if t in ("label", "glabel"):
                name = name or str(c.param(pid, "name", "")).strip() or None
                continue
            if t in C.ANNOT:
                continue
            if t == "mcu":
                p = c.pins.get(port, {})
                nodes.append({"ref": pid, "pin": port, "name": p.get("label", port)})
            else:
                for num in S.pins_of_port(t, port) or [port]:
                    pn = next((x["name"] for x in (S.entry(t) or {}).get("pins", []) if x["num"] == num), port)
                    nodes.append({"ref": pid, "pin": num, "name": pn})
        if not nodes:
            continue
        nodes.sort(key=lambda n: (n["ref"], int(n["pin"]) if n["pin"].isdigit() else 0))
        out.append({"name": name or f"Net-({nodes[0]['ref']}-Pad{nodes[0]['pin']})", "nodes": nodes})
    out.sort(key=lambda n: (n["name"].startswith("Net-"), n["name"]))
    for i, n in enumerate(out, 1):
        n["code"] = i
    return out


def components(d: dict) -> list[dict]:
    out = []
    for p in d.get("parts", []):
        t = p.get("type")
        if t in C.ANNOT or t in C.POWER_SYM:
            continue
        lib = C.LIB.get(t)
        prm = p.get("params") or {}
        value = prm.get("value") or (d.get("device") if t == "mcu" else _value(t, prm, lib))
        fp = prm.get("footprint") or ((C._sym(t)["footprint"]) if lib else "")
        if t == "mcu" and not prm.get("footprint"):
            fp = {100: "Package_QFP:TQFP-100_12x12mm_P0.4mm", 64: "Package_QFP:TQFP-64_10x10mm_P0.5mm",
                  44: "Package_QFP:TQFP-44_10x10mm_P0.8mm", 40: "Package_DIP:DIP-40_W15.24mm",
                  28: "Package_DIP:DIP-28_W7.62mm"}.get(_pincount(d), "")
        out.append({"ref": p["id"], "type": t, "value": str(value), "footprint": fp,
                    "label": lib.label if lib else (d.get("device") if t == "mcu" else t)})
    return out


def _pincount(d):
    try:
        from . import pinout as PO
        return len(PO.get(d.get("device") or "PIC24FJ256GB110")["pins"])
    except Exception:
        return 0


def _value(t, prm, lib):
    if t == "resistor":
        o = float(prm.get("ohms", 330))
        return f"{o / 1e6:g}M" if o >= 1e6 else f"{o / 1e3:g}k" if o >= 1e3 else f"{o:g}"
    if t in ("capacitor", "ecap"):
        f = float(prm.get("farads", 1e-7))
        return f"{f * 1e6:g}u" if f >= 1e-6 else f"{f * 1e9:g}n" if f >= 1e-9 else f"{f * 1e12:g}p"
    return prm.get("part") or (lib.label if lib else t)


def kicad_net(d: dict, packs: str = "") -> str:
    """KiCad 'kicadsexpr' netlist (version E) - Pcbnew can import it with File > Import > Netlist."""
    q = lambda s: '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
    L = ["(export (version \"E\")", "  (design", f"    (source {q((d.get('name') or 'circuit') + '.kicad_sch')})",
         f"    (date {q(time.strftime('%Y-%m-%d %H:%M:%S'))})", "    (tool \"ChipLlama\"))", "  (components"]
    for c in components(d):
        L.append(f"    (comp (ref {q(c['ref'])}) (value {q(c['value'])}) (footprint {q(c['footprint'])})"
                 f" (libsource (lib \"chipllama\") (part {q(c['type'])}) (description {q(c['label'])})))")
    L.append("  )")
    L.append("  (nets")
    for n in nets(d, packs):
        nodes = " ".join(f"(node (ref {q(x['ref'])}) (pin {q(x['pin'])}) (pinfunction {q(x['name'])}))" for x in n["nodes"])
        L.append(f"    (net (code {q(n['code'])}) (name {q(n['name'])}) {nodes})")
    L.append("  ))")
    return "\n".join(L) + "\n"


def netlist_json(d: dict, packs: str = "") -> dict:
    return {"name": d.get("name") or "circuit", "device": d.get("device"), "components": components(d), "nets": nets(d, packs)}


# ---------------------------------------------------------------- MCU power checklist
def mcu_checklist(d: dict, packs: str = "") -> list[dict]:
    c = Circuit(d, packs)
    ics = str(d.get("ics") or "PGx2")
    n = re.sub(r"\D", "", ics) or "2"
    items = []
    by = {}
    for num, p in c.pins.items():
        if p.get("power"):
            by.setdefault(p["power"], []).append(num)

    def ok_rail(num, v):
        net = c.net(f"{c.mcu}.{num}")
        return len(net) > 1 and c.rail(net) == v

    for pw, txt, v in (("vdd", "VDD / AVDD -> +3V3, 100 nF ceramic to GND at each pin (p.27)", 3.3),
                       ("vss", "VSS / AVSS -> GND (p.27)", 0.0), ("envreg", "ENVREG -> VDD enables the core regulator (p.293)", 3.3)):
        if pw in by:
            items.append({"key": pw, "text": txt, "pins": by[pw], "done": all(ok_rail(x, v) for x in by[pw])})
    if "vcap" in by:
        x = by["vcap"][0]
        net = c.net(f"{c.mcu}.{x}")
        caps = [p for p, _ in c.others(net) if c.ptype(p) in ("capacitor", "ecap")]
        uf = max([float(c.param(p, "farads", 0)) * 1e6 for p in caps] or [0])
        items.append({"key": "vcap", "text": "VCAP -> 10 uF low-ESR (< 5 ohm) to GND, never to VDD (p.29)", "pins": by["vcap"],
                      "done": uf >= 4.7})
    if "mclr" in by:
        x = by["mclr"][0]
        items.append({"key": "mclr", "text": "MCLR -> 10 k pull-up to +3V3 (+100 nF optional), to ICSP pin 1 (p.28)", "pins": by["mclr"],
                      "done": c.has_pullup(c.net(f"{c.mcu}.{x}"))})
    pg = [num for num, p in c.pins.items() if any(nm.upper() in (f"PGED{n}", f"PGEC{n}", f"PGD{n}", f"PGC{n}") for nm in p["names"])]
    has_icsp = any(c.ptype(p) == "icsp" for p in c.parts)
    items.append({"key": "icsp", "text": f"ICSP header: MCLR, VDD, VSS, PGED{n}/PGEC{n} ({ics})", "pins": pg, "done": has_icsp})
    return items


# ---------------------------------------------------------------- auto-wire
ROLE_NEED = {"out": "GPIO_OUT", "in": "GPIO_IN", "io": "GPIO_IO", "cs": "GPIO_OUT"}


class _Auto:
    def __init__(self, d, packs):
        self.d = migrate(d)
        self.packs = packs
        self.c = Circuit(self.d, packs)
        self.mcu = self.c.mcu
        self.added: list[str] = []
        self.notes: list[str] = []
        used = set()
        for w in self.d.get("wires", []):
            for ep in (w["a"], w["b"]):
                pid, _, port = ep.partition(".")
                if pid == self.mcu:
                    used.add(port)
        self.used = used
        ics = str(self.d.get("ics") or "PGx2")
        n = re.sub(r"\D", "", ics) or "2"
        self.reserved = {num for num, p in self.c.pins.items()
                         if any(nm.upper() in (f"PGED{n}", f"PGEC{n}", "SOSCI", "SOSCO", "D+", "D-", "VBUS", "VUSB") for nm in p["names"])}

    # placement helpers
    def ref(self, prefix):
        i = 1
        ids = {p["id"] for p in self.d["parts"]}
        while f"{prefix}{i}" in ids:
            i += 1
        return f"{prefix}{i}"

    def part(self, t, x, y, **params):
        lib = C.LIB[t]
        pid = self.ref(re.sub(r"[^A-Za-z]", "", lib.prefix) or "X") if t not in C.POWER_SYM else self.ref("PWR")
        self.d["parts"].append({"id": pid, "type": t, "params": {**lib.params, **params}, "x": round(x / 10) * 10, "y": round(y / 10) * 10})
        self.added.append(pid)
        return pid

    def wire(self, a, b):
        self.d["wires"].append({"a": a, "b": b})

    def pwr(self, t, x, y):
        return self.part(t, x, y) + ".1"

    # pin choice
    def free_pin(self, role, prefer_cn=False, fn_prefix=None):
        cands = []
        for num, p in self.c.pins.items():
            if p.get("power") or not p.get("port") or num in self.used or num in self.reserved:
                continue
            note = str(p.get("note") or "").lower()
            if "do not use" in note or ("input only" in note and role not in ("in", "an", "rx")):
                continue  # e.g. ESP32 SPI-flash pins, input-only GPIO34..39
            if prefer_cn and p.get("pullup") is False:
                continue  # a button needs a pin with an internal pull-up
            v = self.c.valid_functions(num)
            if role in ROLE_NEED:
                ok = True
            elif role == "an":
                ok = any(f.startswith("AN") for f in v)
            elif role == "pwm":
                ok = any(re.fullmatch(r"OC\d|CCP\d|PWM\d[HL]?", f) for f in v)
            elif role in ("rx", "tx"):
                ok = any(re.fullmatch(r"U\dTX|TX\d?" if role == "rx" else r"U\dRX|RX\d?", f) for f in v)
            elif role in ("scl", "sda"):
                ok = any(f.startswith(role.upper() + (fn_prefix or "")) for f in v)
            elif role == "sck":
                ok = any(f.startswith("SCK") for f in v)
            elif role == "mosi":
                ok = any(f.startswith("SDO") for f in v)
            elif role == "miso":
                ok = any(f.startswith("SDI") for f in v)
            else:
                ok = False
            if not ok:
                continue
            score = len(v) if role in ROLE_NEED else 0
            if role in ROLE_NEED and any(f.startswith("AN") for f in v):
                score += 20                     # keep analog pins for analog parts
            if prefer_cn and self.c.weak_pullup_ok(num):
                score -= 50
            if p.get("cn") is not None and prefer_cn:
                score -= 30
            cands.append((score, int(num) if num.isdigit() else 999, num))
        if not cands:
            return None
        num = sorted(cands)[0][2]
        self.used.add(num)
        return num

    def shared_bus_pin(self, role):
        """Reuse an existing I2C / SPI line so several devices share one bus."""
        b = next((b for b in self.c.run()["bindings"] for po, pb in b["pins"].items() if pb["role"] == role), None)
        if b:
            return next(pb["pin"] for pb in b["pins"].values() if pb["role"] == role)
        return None

    def mcu_ep(self, num):
        return f"{self.mcu}.{num}"


def autowire(d: dict, pid: str, packs: str = "") -> dict:
    """Place the supporting parts a part needs (series R, pull-ups, transistor, flyback diode, decoupling) and wire it to
    suggested free MCU pins.  Returns {ok, circuit, added, notes, check}."""
    A = _Auto(d, packs)
    pd = next((p for p in A.d["parts"] if p["id"] == pid), None)
    if not pd:
        return {"ok": False, "reason": f"no part {pid}"}
    t = pd.get("type")
    x, y = float(pd.get("x", 0)), float(pd.get("y", 0))
    E = lambda num: f"{pid}.{num}"
    connected = {w[k] for w in A.d["wires"] for k in ("a", "b")}

    def free(ep):
        return ep not in connected

    if t == "mcu":
        _auto_mcu(A, x, y)
    elif t == "led":
        n = A.free_pin("out")
        r = A.part("resistor", x - 80, y, ohms=330)
        A.wire(A.mcu_ep(n), f"{r}.1"); A.wire(f"{r}.2", E("2")); A.wire(E("1"), A.pwr("gnd", x + 40, y + 60))
        A.notes.append(f"{pid}: {A.c.label(n)} -> {r} 330 ohm -> A; K -> GND (~4 mA).")
    elif t == "rgb_led":
        for num, col in (("1", "R"), ("3", "G"), ("4", "B")):
            n = A.free_pin("pwm") or A.free_pin("out")
            r = A.part("resistor", x - 80, y + 30 * int(num), ohms=330)
            A.wire(A.mcu_ep(n), f"{r}.1"); A.wire(f"{r}.2", E(num))
        A.wire(E("2"), A.pwr("gnd", x + 40, y + 120))
    elif t in ("button", "switch"):
        n = A.free_pin("in", prefer_cn=True)
        A.wire(A.mcu_ep(n), E("1")); A.wire(E("3" if t == "button" else "2"), A.pwr("gnd", x + 40, y + 70))
        A.notes.append(f"{pid}: pin 1 -> {A.c.label(n)} (enable the internal pull-up / CN), pin {'3' if t == 'button' else '2'} -> GND.")
    elif t == "spdt":
        n = A.free_pin("in")
        A.wire(A.mcu_ep(n), E("2")); A.wire(E("1"), A.pwr("vdd33", x - 20, y - 50)); A.wire(E("3"), A.pwr("gnd", x + 40, y + 60))
    elif t in ("buzzer_active", "relay"):
        q = A.part("npn", x, y + 110)
        r = A.part("resistor", x - 90, y + 110, ohms=1000)
        n = A.free_pin("out")
        A.wire(A.mcu_ep(n), f"{r}.1"); A.wire(f"{r}.2", f"{q}.2"); A.wire(f"{q}.1", A.pwr("gnd", x + 10, y + 190))
        top, bot = ("1", "2")
        A.wire(E(bot), f"{q}.3"); A.wire(E(top), A.pwr("v5", x, y - 60))
        if t == "relay":
            dd = A.part("diode", x + 90, y + 20)
            A.wire(f"{dd}.1", E(top)); A.wire(f"{dd}.2", E(bot))
        A.notes.append(f"{pid}: low-side NPN {q} with 1 k base resistor {r} from {A.c.label(n)}" + (", 1N4148 flyback across the coil." if t == "relay" else "."))
    elif t == "dc_motor":
        q = A.part("nmos", x, y + 110)
        rg = A.part("resistor", x - 90, y + 110, ohms=100)
        rpd = A.part("resistor", x - 40, y + 170, ohms=10000)
        dd = A.part("diode", x + 90, y + 20)
        n = A.free_pin("pwm") or A.free_pin("out")
        gnd = A.pwr("gnd", x + 10, y + 220)
        A.wire(A.mcu_ep(n), f"{rg}.1"); A.wire(f"{rg}.2", f"{q}.1"); A.wire(f"{q}.1", f"{rpd}.1"); A.wire(f"{rpd}.2", gnd)
        A.wire(f"{q}.3", gnd); A.wire(E("2"), f"{q}.2"); A.wire(E("1"), A.pwr("v5", x, y - 60))
        A.wire(f"{dd}.1", E("1")); A.wire(f"{dd}.2", E("2"))
    elif t in ("npn", "nmos"):
        r = A.part("resistor", x - 90, y, ohms=1000 if t == "npn" else 100)
        n = A.free_pin("out")
        A.wire(A.mcu_ep(n), f"{r}.1"); A.wire(f"{r}.2", E("2" if t == "npn" else "1"))
        A.wire(E("1" if t == "npn" else "3"), A.pwr("gnd", x, y + 80))
    elif t == "pot":
        n = A.free_pin("an")
        A.wire(A.mcu_ep(n), E("2")); A.wire(E("1"), A.pwr("gnd", x, y + 70)); A.wire(E("3"), A.pwr("vdd33", x + 40, y - 50))
    elif t in ("ldr", "ntc"):
        n = A.free_pin("an")
        r = A.part("resistor", x + 60, y + 60, ohms=10000)
        A.wire(E("1"), A.pwr("vdd33", x - 20, y - 50)); A.wire(E("2"), A.mcu_ep(n)); A.wire(E("2"), f"{r}.1"); A.wire(f"{r}.2", A.pwr("gnd", x + 60, y + 130))
    elif t == "crystal":
        si = next((k for k, p in A.c.pins.items() if "SOSCI" in [s.upper() for s in p["names"]]), None)
        so = next((k for k, p in A.c.pins.items() if "SOSCO" in [s.upper() for s in p["names"]]), None)
        if si and so:
            A.wire(E("1"), A.mcu_ep(si)); A.wire(E("2"), A.mcu_ep(so))
            for num, dx in (("1", -40), ("2", 40)):
                cc = A.part("capacitor", x + dx, y + 60, farads=22e-12)
                A.wire(E(num), f"{cc}.1"); A.wire(f"{cc}.2", A.pwr("gnd", x + dx, y + 120))
    elif t == "opto":
        n = A.free_pin("out")
        r = A.part("resistor", x - 90, y, ohms=330)
        A.wire(A.mcu_ep(n), f"{r}.1"); A.wire(f"{r}.2", E("1")); A.wire(E("2"), A.pwr("gnd", x - 20, y + 80))
    else:
        _auto_module(A, pd, x, y, free)
    A.d["schema"] = 2
    from . import geometry as GM
    base = A.d
    for mv in (set(A.added) | {pid}, set(A.added), None):
        # least-disruptive first: move only the new helpers, then the part itself, then everything but the MCU
        A.d = GM.tidy(base, mv, packs)
        au = GM.audit(A.d, packs)
        if not au["problems"] and not au["dangling"] and not A.d.get("unrouted"):
            break
    res = check(A.d, packs)
    return {"ok": True, "circuit": A.d, "added": A.added, "notes": A.notes, "check": {"ok": res["ok"], "findings": res["findings"]}}


def _auto_module(A: _Auto, pd, x, y, free):
    pid, t = pd["id"], pd["type"]
    lib = C.LIB.get(t)
    e = S.entry(t)
    if not lib or not e:
        A.notes.append(f"{pid}: nothing to auto-wire.")
        return
    roles = dict(lib.ports)
    vrail = "v5" if (lib.logic_v or 3.3) > 3.6 or t in ("servo", "ws2812", "hc05", "lcd1602", "relay_module", "mcp2515") else "vdd33"
    gnd = vcc = None
    uart_pins = {}
    ym = y
    for pin in e["pins"]:
        ep = f"{pid}.{pin['num']}"
        if not free(ep):
            continue
        port, role = pin["port"], roles.get(pin["port"], "p")
        if any(pin["num"] != q["num"] and q["port"] == port and not free(f"{pid}.{q['num']}") for q in e["pins"]):
            continue
        if role == "gnd" or port in ("GND", "VSS") or (t == "lcd1602" and port in ("RW", "K")) or (t == "eeprom24" and port in ("A0", "A1", "A2", "WP")) \
                or (t == "mpu6050" and port == "AD0") or (t == "hc595" and port == "OE"):
            gnd = gnd or A.pwr("gnd", x - 60, y + 40 * len(e["pins"]) // 2 + 40)
            A.wire(ep, gnd)
        elif role == "vcc" or port in ("VCC", "VDD", "V+", "VS", "+") or (t == "lcd1602" and port == "A") or (t == "hc595" and port == "SRCLR") \
                or (t == "flash25" and port in ("WP", "HOLD")):
            vcc = vcc or A.pwr(vrail if port != "VDD" or t != "stepper_a4988" else "vdd33", x - 60, y - 50)
            A.wire(ep, vcc)
        elif t == "lcd1602" and port == "VO":
            pot = A.part("pot", x - 150, y + 60, ohms=10000)
            A.wire(ep, f"{pot}.2"); A.wire(f"{pot}.1", A.pwr("gnd", x - 150, y + 130)); A.wire(f"{pot}.3", A.pwr("v5", x - 150, y))
            A.notes.append(f"{pid}: contrast pot {pot} on VO.")
        elif role in ("scl", "sda"):
            n = A.shared_bus_pin(role) or A.free_pin(role, fn_prefix=uart_pins.get("i2c", ""))
            if n is None:
                A.notes.append(f"{pid}.{port}: no free {role.upper()} pin.")
                continue
            m = re.search(r"\d", next((f for f in A.c.valid_functions(n) if f.startswith(role.upper())), "") or "")
            uart_pins["i2c"] = m.group(0) if m else ""
            A.wire(ep, A.mcu_ep(n))
            if not A.c.has_pullup(A.c.net(A.mcu_ep(n))) and not any(w for w in A.d["wires"] if w["a"] == A.mcu_ep(n) and w["b"].split(".")[0] in A.added):
                r = A.part("resistor", x - 120, ym - 60, ohms=4700)
                ym += 40
                A.wire(f"{r}.1", A.mcu_ep(n)); A.wire(f"{r}.2", A.pwr("vdd33", x - 120, ym - 140))
                A.notes.append(f"{pid}.{port}: 4.7 k pull-up {r} to +3V3.")
        elif role in ("sck", "mosi", "miso"):
            n = A.shared_bus_pin(role) or A.free_pin(role)
            if n:
                A.wire(ep, A.mcu_ep(n))
        elif role in ("rx", "tx", "out", "in", "io", "an", "pwm", "cs"):
            n = A.free_pin(role, prefer_cn=role == "in")
            if n is None:
                A.notes.append(f"{pid}.{port}: no free pin for {role}.")
                continue
            if t == "ws2812" and port == "DIN":
                r = A.part("resistor", x - 100, y, ohms=330)
                A.wire(A.mcu_ep(n), f"{r}.1"); A.wire(f"{r}.2", ep)
            else:
                A.wire(ep, A.mcu_ep(n))
            if role == "io":
                r = A.part("resistor", x - 100, y - 60, ohms=10000 if t == "dht22" else 4700)
                A.wire(f"{r}.1", ep); A.wire(f"{r}.2", A.pwr("vdd33", x - 100, y - 120))
                A.notes.append(f"{pid}.{port}: pull-up {r} to +3V3.")
        elif port == "NC" or t in ("hc05", "mpu6050", "uart_usb") and role == "p":
            A.wire(ep, A.part("noconnect", x - 30, y) + ".1")
    if t == "rs485":
        A.wire(f"{pid}.2", f"{pid}.3")
        A.notes.append(f"{pid}: ~RE tied to DE (one GPIO switches direction).")
    if any(r in ("vcc",) for r in roles.values()) and t in ("hc595", "eeprom24", "flash25", "mcp2515"):
        cc = A.part("capacitor", x - 160, y - 40, farads=1e-7)
        A.wire(f"{cc}.1", vcc or A.pwr(vrail, x - 160, y - 90)); A.wire(f"{cc}.2", gnd or A.pwr("gnd", x - 160, y + 20))
        A.notes.append(f"{pid}: 100 nF decoupling {cc} on VCC.")


def _auto_mcu(A: _Auto, x, y):
    c = A.c
    done = {w[k] for w in A.d["wires"] for k in ("a", "b")}
    col = x - 160
    k = 0
    gnd = None
    for num, p in sorted(c.pins.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        pw = p.get("power")
        ep = A.mcu_ep(num)
        if not pw or ep in done:
            continue
        yy = y + 30 * k
        k += 1
        if pw in ("vdd", "envreg"):
            v = A.pwr("vdd33", col, yy - 40)
            A.wire(ep, v)
            if pw == "vdd":
                cc = A.part("capacitor", col - 60, yy, farads=1e-7)
                A.wire(f"{cc}.1", ep); gnd = gnd or A.pwr("gnd", col - 60, yy + 60); A.wire(f"{cc}.2", gnd)
        elif pw == "vss":
            gnd = gnd or A.pwr("gnd", col, yy + 40)
            A.wire(ep, gnd)
        elif pw == "vcap":
            cc = A.part("ecap", col - 60, yy, farads=10e-6)
            A.wire(ep, f"{cc}.1"); gnd = gnd or A.pwr("gnd", col - 60, yy + 60); A.wire(f"{cc}.2", gnd)
            A.notes.append(f"VCAP pin {num}: 10 uF low-ESR {cc} to GND (p.29).")
        elif pw == "mclr":
            r = A.part("resistor", col - 60, yy - 30, ohms=10000)
            A.wire(ep, f"{r}.1"); A.wire(f"{r}.2", A.pwr("vdd33", col - 60, yy - 80))
            j = A.part("icsp", col - 200, yy)
            A.wire(f"{j}.1", ep); A.wire(f"{j}.2", A.pwr("vdd33", col - 200, yy - 50)); gnd = gnd or A.pwr("gnd", col - 200, yy + 150)
            A.wire(f"{j}.3", gnd)
            ics = re.sub(r"\D", "", str(A.d.get("ics") or "PGx2")) or "2"
            for hp, names in (("4", (f"PGED{ics}", f"PGD{ics}")), ("5", (f"PGEC{ics}", f"PGC{ics}"))):
                pn = next((n for n, q in c.pins.items() if any(s.upper() in names for s in q["names"])), None)
                if pn:
                    A.wire(f"{j}.{hp}", A.mcu_ep(pn))
            A.wire(f"{j}.6", A.part("noconnect", col - 230, yy + 100) + ".1")
            A.notes.append(f"MCLR pin {num}: 10 k pull-up {r}; ICSP header {j} on MCLR/VDD/VSS/PGx{ics}.")
        elif pw in ("vusb",):
            A.wire(ep, A.pwr("vdd33", col, yy - 40))
    A.d["power"] = "manual"
    A.notes.append("Power set to 'manual': the ERC now checks every VDD/VSS/VCAP/MCLR pin.")


# ---------------------------------------------------------------- per-part guide
def guide(t: str) -> dict:
    e = S.entry(t) or {}
    lib = C.LIB.get(t)
    return {"type": t, "label": lib.label if lib else t, "guide": e.get("guide", ""), "pins": e.get("pins", []),
            "shorts": e.get("shorts", [])}


def dumps(d):
    return json.dumps(d, indent=1)
