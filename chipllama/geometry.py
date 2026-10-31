"""Schematic geometry (M7.1) - the single source of truth for where every pin end, symbol body and field sits.

Units: 1 unit = 5 mil, GRID = 10 units = 50 mil (KiCad's default schematic grid); pins are 100 mil (20 units) long
from the body to the connection point, and every connection point lands exactly on the 50 mil grid in every
rotation / mirror.  The editor (circuit.js) draws from the same numbers (served in /api/circuit/meta), the router
below uses them to draw orthogonal wires that never cross a symbol body or a field, and the tests use them to
check the drawing geometrically (wire ends sit on pin ends, connectivity from geometry == netlist).
"""
from __future__ import annotations

import copy
import heapq
import math
import re

from . import components as C, pinout as PO, symbols as S

GRID = 10
PP = 20          # pin pitch (100 mil)
MCU_PIN = 30     # MCU pin length (150 mil)
PIN = 20         # primitive pin length (100 mil)


def snap(v):
    return int(round(v / GRID) * GRID)


def _P(num, name, x, y, dx, dy, ln):
    return {"num": str(num), "name": name, "x": x, "y": y, "dx": dx, "dy": dy, "len": ln}


def _ceil20(v):
    return int(math.ceil(v / 20.0) * 20)


# ---------------------------------------------------------------- symbol geometry (local coords, origin = anchor)
def sym_geo(t: str) -> dict:
    """{kind, pins:[{num,name,x,y,dx,dy,len}], box:[x0,y0,x1,y1], body?} - box contains the drawing and pin lines;
    pin ends lie on the box edge, pointing out of it."""
    e = S.entry(t) or {}
    k = e.get("kind") or "module"
    pins = e.get("pins") or []
    by = {p["name"]: p["num"] for p in pins}
    num = lambda n: by.get(n, n)
    g = {"kind": k, "pins": []}

    def two(half, ln, box):
        g["pins"] = [_P(pins[0]["num"], pins[0]["name"], 0, -half, 0, -1, ln), _P(pins[1]["num"], pins[1]["name"], 0, half, 0, 1, ln)]
        g["box"] = box

    if k in ("res", "ldr", "ntc"):
        two(40, PIN, {"res": [-10, -40, 10, 40], "ldr": [-30, -40, 10, 40], "ntc": [-16, -40, 22, 40]}[k])
    elif k in ("cap", "ecap"):
        two(30, 26, [-14, -30, 14, 30])
    elif k in ("led", "diode", "zener"):
        g["pins"] = [_P(num("A"), "A", 0, -40, 0, -1, 25), _P(num("K"), "K", 0, 40, 0, 1, 25)]
        g["box"] = [-14, -40, 32 if k == "led" else 14, 40]
    elif k == "battery":
        g["pins"] = [_P(num("+"), "+", 0, -40, 0, -1, 30), _P(num("-"), "-", 0, 40, 0, 1, 30)]
        g["box"] = [-16, -40, 16, 40]
    elif k in ("buzzer", "motor"):
        g["pins"] = [_P(pins[0]["num"], pins[0]["name"], 0, -60, 0, -1, 26), _P(pins[1]["num"], pins[1]["name"], 0, 60, 0, 1, 26)]
        g["box"] = [-22, -60, 22, 60]
    elif k == "xtal":
        g["pins"] = [_P(pins[0]["num"], pins[0]["name"], -40, 0, -1, 0, 30), _P(pins[1]["num"], pins[1]["name"], 40, 0, 1, 0, 30)]
        g["box"] = [-40, -16, 40, 16]
    elif k == "spst":
        g["pins"] = [_P(pins[0]["num"], pins[0]["name"], -40, 0, -1, 0, PIN), _P(pins[1]["num"], pins[1]["name"], 40, 0, 1, 0, PIN)]
        g["box"] = [-40, -16, 40, 8]
    elif k == "spdt":
        g["pins"] = [_P(num("COM"), "COM", -40, 0, -1, 0, PIN), _P(num("A"), "A", 40, -20, 1, 0, PIN), _P(num("B"), "B", 40, 20, 1, 0, PIN)]
        g["box"] = [-40, -26, 40, 26]
    elif k == "btn4":
        g["pins"] = [_P("1", "1", -40, -20, -1, 0, PIN), _P("2", "2", -40, 20, -1, 0, PIN), _P("3", "3", 40, -20, 1, 0, PIN), _P("4", "4", 40, 20, 1, 0, PIN)]
        g["box"] = [-40, -30, 40, 26]
    elif k == "pot":
        g["pins"] = [_P(pins[2]["num"], pins[2]["name"], 0, -40, 0, -1, PIN), _P(pins[0]["num"], pins[0]["name"], 0, 40, 0, 1, PIN),
                     _P(num("W"), "W", 40, 0, 1, 0, 28)]
        g["box"] = [-12, -40, 40, 40]
    elif k in ("npn", "pnp", "nmos", "pmos"):
        up, dn = {"npn": ("C", "E"), "pnp": ("E", "C"), "nmos": ("D", "S"), "pmos": ("S", "D")}[k]
        b = "B" if k in ("npn", "pnp") else "G"
        g["pins"] = [_P(num(b), b, -40, 0, -1, 0, 26), _P(num(up), up, 10, -40, 0, -1, 22), _P(num(dn), dn, 10, 40, 0, 1, 22)]
        g["box"] = [-40, -40, 22, 40]
    elif k == "relay":
        g["pins"] = [_P(num("COIL+"), "COIL+", -30, -50, 0, -1, PIN), _P(num("COIL-"), "COIL-", -30, 50, 0, 1, PIN),
                     _P(num("COM"), "COM", 30, 50, 0, 1, PIN), _P(num("NC"), "NC", 20, -50, 0, -1, PIN), _P(num("NO"), "NO", 40, -50, 0, -1, PIN)]
        g["box"] = [-56, -50, 62, 50]
    elif k == "reg":
        g["pins"] = [_P(num("VIN"), "VIN", -70, 0, -1, 0, PIN), _P(num("VOUT"), "VOUT", 70, 0, 1, 0, PIN), _P(num("GND"), "GND", 0, 50, 0, 1, PIN)]
        g["box"] = [-70, -30, 70, 50]
    elif k == "pwr":
        g["pins"] = [_P(pins[0]["num"], pins[0]["name"], 0, 0, 0, 1, 0)]
        g["box"] = [-16, -38, 16, 0]
    elif k == "gnd":
        g["pins"] = [_P(pins[0]["num"], pins[0]["name"], 0, 0, 0, -1, 0)]
        g["box"] = [-16, 0, 16, 40]
    elif k in ("label", "glabel"):
        g["pins"] = [_P(pins[0]["num"], "~", 0, 0, -1, 0, 0)]
        g["box"] = [0, -14, 80, 4] if k == "label" else [0, -9, 80, 9]
    elif k == "junction":
        g["pins"] = [_P(pins[0]["num"], "~", 0, 0, 0, 0, 0)]
        g["box"] = [-4, -4, 4, 4]
    elif k == "nc":
        g["pins"] = [_P(pins[0]["num"], "~", 0, 0, 0, 0, 0)]
        g["box"] = [-7, -7, 7, 7]
    elif k == "dip":
        n = len(pins)
        L = (n + 1) // 2
        W = max(80, _ceil20(max(len(p["name"]) for p in pins) * 12 + 30))
        H = L * PP + 20
        for i, p in enumerate(pins):
            left = i < L
            kk = i if left else n - 1 - i
            y = snap(-H / 2 + 20 + kk * PP)
            g["pins"].append(_P(p["num"], p["name"], -W // 2 - 30 if left else W // 2 + 30, y, -1 if left else 1, 0, 30))
        g["body"] = [-W // 2, -H // 2, W // 2, H // 2]
        g["box"] = [-W // 2 - 30, -H // 2, W // 2 + 30, H // 2]
    elif k == "conn":
        n = len(pins)
        H = n * PP
        for i, p in enumerate(pins):
            g["pins"].append(_P(p["num"], p["name"], -40, -H // 2 + 10 + i * PP, -1, 0, 30))
        g["body"] = [-10, -H // 2, 10, H // 2]
        g["box"] = [-40, -H // 2, 10, H // 2]
    else:
        n = len(pins)
        H = n * PP + 20
        W = max(100, _ceil20(max([len(p["name"]) for p in pins] + [1]) * 8 + 60))
        for i, p in enumerate(pins):
            g["pins"].append(_P(p["num"], p["name"], -W // 2 - 30, -H // 2 + 20 + i * PP, -1, 0, 30))
        g["body"] = [-W // 2, -H // 2, W // 2, H // 2]
        g["box"] = [-W // 2 - 30, -H // 2, W // 2, H // 2]
        g["kind"] = "module"
    g["pin"] = {p["num"]: p for p in g["pins"]}
    return g


def label_width(name: str, kind: str) -> int:
    n = len(str(name or "NET"))
    return max(40, n * 7 + 18) if kind == "glabel" else n * 7 + 14


# ---------------------------------------------------------------- MCU geometry (KiCad style: power top, VSS bottom, ports L/R)
def mcu_geo(device: str, packs: str = "", info: dict | None = None) -> dict:
    info = info or PO.get(device, packs)
    pins = info["pins"]
    if info.get("board"):
        return _board_geo(info)
    top, bot, ports, other = [], [], {}, []
    for p in pins:
        pw = p.get("power")
        if pw == "vss":
            bot.append(p)
        elif pw:
            top.append(p)
        else:
            m = re.match(r"^R([A-K])(\d+)", str(p.get("label") or ""))
            if m:
                ports.setdefault(m.group(1), []).append((int(m.group(2)), p))
            else:
                other.append(p)
    groups = [[x[1] for x in sorted(ports[k], key=lambda x: x[0])] for k in sorted(ports)]
    if other:
        groups.insert(0, other)
    total = sum(len(g) + 1 for g in groups)
    left, right, cnt = [], [], 0
    for gr in groups:
        (left if cnt < total / 2 else right).append(gr)
        cnt += len(gr) + 1

    def col(gs):
        out = []
        for i, gr in enumerate(gs):
            if i:
                out.append(None)
            out.extend(gr)
        return out

    Lc, Rc = col(left), col(right)
    rows = max(len(Lc), len(Rc), 1)
    W = max(380, (max(len(top), len(bot)) + 1) * PP * 2 + 40)
    H = rows * PP + 80
    g = {"w": W, "h": H, "pins": {}}
    for i, p in enumerate(Lc):
        if p:
            g["pins"][str(p["num"])] = {"x": 0, "y": 60 + i * PP, "dx": -1, "dy": 0}
    for i, p in enumerate(Rc):
        if p:
            g["pins"][str(p["num"])] = {"x": W, "y": 60 + i * PP, "dx": 1, "dy": 0}
    for i, p in enumerate(sorted(top, key=lambda p: p.get("power") or "")):
        g["pins"][str(p["num"])] = {"x": W // 2 - (len(top) - 1) * PP + i * PP * 2, "y": 0, "dx": 0, "dy": -1}
    for i, p in enumerate(bot):
        g["pins"][str(p["num"])] = {"x": W // 2 - (len(bot) - 1) * PP + i * PP * 2, "y": H, "dx": 0, "dy": 1}
    return g


def _board_geo(info: dict) -> dict:
    """Dev-board symbol (M8 families): header pins on the sides they sit on, supplies on top, GND at the bottom."""
    side = {k: sorted([p for p in info["pins"] if p.get("side") == k], key=lambda p: p.get("row", 0)) for k in "LRTB"}
    rows = max(len(side["L"]), len(side["R"]), 1)
    W = max(380, (max(len(side["T"]), len(side["B"])) + 1) * PP * 2 + 40)
    H = rows * PP + 80
    g = {"w": W, "h": H, "pins": {}}
    for i, p in enumerate(side["L"]):
        g["pins"][str(p["num"])] = {"x": 0, "y": 60 + i * PP, "dx": -1, "dy": 0}
    for i, p in enumerate(side["R"]):
        g["pins"][str(p["num"])] = {"x": W, "y": 60 + i * PP, "dx": 1, "dy": 0}
    for k, y, dy in (("T", 0, -1), ("B", H, 1)):
        n = len(side[k])
        for i, p in enumerate(side[k]):
            g["pins"][str(p["num"])] = {"x": W // 2 - (n - 1) * PP + i * PP * 2, "y": y, "dx": 0, "dy": dy}
    return g


# ---------------------------------------------------------------- transforms (same as circuit.js xf/xd)
def xf(pt, x, y):
    if pt.get("mx"):
        x = -x
    if pt.get("my"):
        y = -y
    r = int(pt.get("rot") or 0) % 360
    X, Y = x, y
    if r == 90:
        X, Y = -y, x
    elif r == 180:
        X, Y = -x, -y
    elif r == 270:
        X, Y = y, -x
    return pt.get("x", 0) + X, pt.get("y", 0) + Y


def xd(pt, dx, dy):
    q = {"rot": pt.get("rot"), "mx": pt.get("mx"), "my": pt.get("my"), "x": 0, "y": 0}
    a, b = xf(q, dx, dy)
    return int(round(a)), int(round(b))


class Geo:
    """World geometry of one circuit: pin ends, part boxes, field boxes."""

    def __init__(self, d: dict, packs: str = ""):
        self.d = d
        self.parts = {p["id"]: p for p in d.get("parts", [])}
        self.mcu = mcu_geo(d.get("device") or "PIC24FJ256GB110", packs)
        self._sg = {}

    def sg(self, t):
        if t not in self._sg:
            self._sg[t] = sym_geo(t)
        return self._sg[t]

    def kind(self, pt):
        return "mcu" if pt.get("type") == "mcu" else self.sg(pt["type"])["kind"]

    def local_box(self, pt):
        g = self.sg(pt["type"])
        b = list(g["box"])
        if g["kind"] in ("label", "glabel"):
            b[2] = label_width((pt.get("params") or {}).get("name"), g["kind"])
        return b

    def pin(self, ep):
        pid, _, num = ep.partition(".")
        pt = self.parts.get(pid)
        if not pt:
            return None
        if pt.get("type") == "mcu":
            q = self.mcu["pins"].get(num)
            if not q:
                return None
            return (pt["x"] + q["x"] + q["dx"] * MCU_PIN, pt["y"] + q["y"] + q["dy"] * MCU_PIN, q["dx"], q["dy"])
        q = self.sg(pt["type"])["pin"].get(num)
        if not q:
            return None
        x, y = xf(pt, q["x"], q["y"])
        dx, dy = xd(pt, q["dx"], q["dy"])
        return (int(round(x)), int(round(y)), dx, dy)

    def pins_of(self, pt):
        if pt.get("type") == "mcu":
            return [f"{pt['id']}.{n}" for n in self.mcu["pins"]]
        return [f"{pt['id']}.{p['num']}" for p in self.sg(pt["type"])["pins"]]

    def box(self, pt):
        if pt.get("type") == "mcu":
            m = self.mcu
            return [pt["x"] - MCU_PIN, pt["y"] - MCU_PIN, pt["x"] + m["w"] + MCU_PIN, pt["y"] + m["h"] + MCU_PIN]
        b = self.local_box(pt)
        a, c = xf(pt, b[0], b[1]), xf(pt, b[2], b[3])
        return [min(a[0], c[0]), min(a[1], c[1]), max(a[0], c[0]), max(a[1], c[1])]

    def fields(self, pt):
        """Ref/Value text placement (world, always upright like KiCad): on the side of the box that has no pins.
        Returns {ref:{x,y,anchor}, val:{...}, rect:[x0,y0,x1,y1]} or None for power / labels / MCU."""
        if pt.get("type") == "mcu" or self.kind(pt) in ("pwr", "gnd", "label", "glabel", "junction", "nc"):
            return None
        return field_place(self.box(pt), [self.pin(e)[2:] for e in self.pins_of(pt)], pt["id"], value_of(pt))


def value_of(pt):
    """Text of the Value field (mirrors valueOf in circuit.js closely enough for collision boxes)."""
    p = pt.get("params") or {}
    if p.get("value"):
        return str(p["value"])
    lib = C.LIB.get(pt.get("type"))
    t = pt.get("type")
    if t in ("resistor", "pot", "ldr") and p.get("ohms"):
        return _ohm(p["ohms"])
    if p.get("farads"):
        return "100.0nF"
    if p.get("part"):
        return str(p["part"])
    if t == "led":
        return "LED " + str(p.get("color") or "")
    return lib.label if lib else str(t)


def _ohm(o):
    o = float(o)
    return (f"{o / 1e6:g}M" if o >= 1e6 else f"{o / 1e3:g}k" if o >= 1e3 else f"{o:g}Ω")


def field_place(bb, dirs, ref, val):
    w = max(len(ref) * 7.5, len(val) * 6.2) + 4
    has = {(int(dx), int(dy)) for dx, dy in dirs}
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    if (1, 0) not in has:
        x = bb[2] + 6
        return {"ref": {"x": x, "y": cy - 3, "anchor": "start"}, "val": {"x": x, "y": cy + 11, "anchor": "start"}, "rect": [bb[2] + 2, cy - 15, x + w, cy + 15]}
    if (-1, 0) not in has:
        x = bb[0] - 6
        return {"ref": {"x": x, "y": cy - 3, "anchor": "end"}, "val": {"x": x, "y": cy + 11, "anchor": "end"}, "rect": [x - w, cy - 15, bb[0] - 2, cy + 15]}
    if (0, -1) not in has:
        return {"ref": {"x": cx, "y": bb[1] - 18, "anchor": "middle"}, "val": {"x": cx, "y": bb[1] - 6, "anchor": "middle"}, "rect": [cx - w / 2, bb[1] - 30, cx + w / 2, bb[1] - 2]}
    return {"ref": {"x": cx, "y": bb[3] + 14, "anchor": "middle"}, "val": {"x": cx, "y": bb[3] + 26, "anchor": "middle"}, "rect": [cx - w / 2, bb[3] + 2, cx + w / 2, bb[3] + 30]}


def library_geo() -> dict:
    out = {}
    for p in C.library():
        g = sym_geo(p["type"])
        out[p["type"]] = {"kind": g["kind"], "pins": g["pins"], "box": g["box"], **({"body": g["body"]} if "body" in g else {})}
    return out


# ---------------------------------------------------------------- wire polylines
def wire_path(G: Geo, w):
    A, B = G.pin(w["a"]), G.pin(w["b"])
    if not A or not B:
        return None
    raw = [(A[0], A[1])] + [(p["x"], p["y"]) for p in (w.get("pts") or [])] + [(B[0], B[1])]
    out = [raw[0]]
    horiz = A[3] == 0
    for q in raw[1:]:
        p = out[-1]
        if p[0] != q[0] and p[1] != q[1]:
            out.append((q[0], p[1]) if horiz else (p[0], q[1]))
            horiz = not horiz
        else:
            horiz = p[1] == q[1]
        out.append(q)
    res = [out[0]]
    for p in out[1:]:
        if p != res[-1]:
            res.append(p)
    # drop collinear middle points
    k = [res[0]]
    for i in range(1, len(res) - 1):
        a, b, c = k[-1], res[i], res[i + 1]
        if (a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1]):
            continue
        k.append(b)
    if len(res) > 1:
        k.append(res[-1])
    return k


def grid_points(poly):
    """Every grid point on an orthogonal polyline."""
    pts = []
    for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
        n = max(abs(x1 - x0), abs(y1 - y0)) // GRID
        sx, sy = (x1 > x0) - (x1 < x0), (y1 > y0) - (y1 < y0)
        for i in range(n + (0 if pts else 1)):
            if pts and i == 0:
                continue
            pts.append((x0 + sx * GRID * i, y0 + sy * GRID * i))
        pts.append((x1, y1))
    seen, out = set(), []
    for p in pts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def unit_edges(poly):
    es = set()
    for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
        if y0 == y1:
            for x in range(min(x0, x1), max(x0, x1), GRID):
                es.add(("h", x, y0))
        else:
            for y in range(min(y0, y1), max(y0, y1), GRID):
                es.add(("v", x0, y))
    return es


# ---------------------------------------------------------------- router
class Router:
    """Orthogonal A* router on the 50 mil grid.  Wires never enter a symbol box or field box (inflated by one grid),
    never run on top of another wire, cross other wires only straight through (no bend, no end), and leave a pin in
    the pin's own direction.  A second wire on an already-wired endpoint may branch off that endpoint's existing
    wire: a junction is created there and the wire is split (exactly like KiCad)."""

    TURN = 6
    CROSS = 8

    def __init__(self, d, packs=""):
        self.d = d
        self.G = Geo(d, packs)
        self.blocked_by = {}        # point -> set(part ids) whose inflated box contains it strictly
        self.edges = {}             # unit edge -> wire index
        self.points = {}            # grid point -> (wire index, orientation 'h'/'v'/'x' (corner/end))
        self.pin_tips = {}
        G = self.G
        xs, ys = [], []
        for pt in d["parts"]:
            b = G.box(pt)
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
            rects = [(b, 10)]
            f = G.fields(pt)
            if f:
                rects.append((f["rect"], 4))
                xs += [f["rect"][0], f["rect"][2]]
                ys += [f["rect"][1], f["rect"][3]]
            for r, inf in rects:
                x0, y0 = snap_dn(r[0] - inf), snap_dn(r[1] - inf)
                x1, y1 = snap_up(r[2] + inf), snap_up(r[3] + inf)
                for x in range(x0 + GRID, x1, GRID):
                    for y in range(y0 + GRID, y1, GRID):
                        self.blocked_by.setdefault((x, y), set()).add(pt["id"])
            for ep in G.pins_of(pt):
                p = G.pin(ep)
                if p:
                    self.pin_tips[(p[0], p[1])] = ep
        self.bounds = (snap_dn(min(xs or [0]) - 200), snap_dn(min(ys or [0]) - 200), snap_up(max(xs or [0]) + 200), snap_up(max(ys or [0]) + 200))

    # occupancy
    def occupy(self, i, poly):
        for e in unit_edges(poly):
            self.edges[e] = i
        corners = set(poly)
        for p in grid_points(poly):
            if p in corners:
                self.points[p] = (i, "x")
            else:
                self.points[p] = (i, "h" if any(poly[k][1] == poly[k + 1][1] == p[1] and min(poly[k][0], poly[k + 1][0]) <= p[0] <= max(poly[k][0], poly[k + 1][0]) for k in range(len(poly) - 1)) else "v")

    def release(self, i, poly):
        for e in unit_edges(poly):
            if self.edges.get(e) == i:
                del self.edges[e]
        for p in grid_points(poly):
            if self.points.get(p, (None,))[0] == i:
                del self.points[p]

    def route(self, src, dst, allow_parts=(), first_dir=None, last_dir=None):
        """src/dst: list of (point, start_dirs).  Returns list of grid points (src..dst) or None."""
        x0, y0, x1, y1 = self.bounds
        goals = {p: dirs for p, dirs in dst}
        allow = set(allow_parts)

        def free(p):
            b = self.blocked_by.get(p)
            return not b or b <= allow and False or not b - allow and False or not b

        def ok_point(p):
            b = self.blocked_by.get(p)
            if b and not (b <= allow and self._ann(allow)):
                return False
            return True

        DIRS = ((GRID, 0), (-GRID, 0), (0, GRID), (0, -GRID))
        h = lambda p: min(abs(p[0] - g[0]) + abs(p[1] - g[1]) for g in goals) // GRID
        openq, best, prev = [], {}, {}
        for p, dirs in src:
            for d in dirs:
                s = (p, d)
                best[s] = 0
                heapq.heappush(openq, (h(p), 0, p, d))
                prev[s] = None
        while openq:
            f, gc, p, d = heapq.heappop(openq)
            if best.get((p, d), 1e18) < gc:
                continue
            if p in goals and (p, d) in prev and prev[(p, d)] is not None:
                gd = goals[p]
                if not gd or (-d[0], -d[1]) in gd:
                    path = [p]
                    s = (p, d)
                    while prev[s] is not None:
                        s = prev[s]
                        path.append(s[0])
                    return path[::-1]
            on = self.points.get(p)
            crossing = on is not None and (p, d) in prev and prev[(p, d)] is not None
            for nd in DIRS:
                if nd[0] == -d[0] and nd[1] == -d[1]:
                    continue
                if crossing and nd != d:
                    continue                       # cross straight through, never bend on another wire
                if prev.get((p, d)) is None and nd != d:
                    continue                       # leave in the start direction
                q = (p[0] + nd[0], p[1] + nd[1])
                if not (x0 <= q[0] <= x1 and y0 <= q[1] <= y1):
                    continue
                e = ("h", min(p[0], q[0]), p[1]) if nd[1] == 0 else ("v", p[0], min(p[1], q[1]))
                if e in self.edges:
                    continue
                if q not in goals:
                    if not ok_point(q):
                        continue
                    if q in self.pin_tips:
                        continue
                    o = self.points.get(q)
                    if o is not None and (o[1] == "x" or o[1] == ("h" if nd[1] == 0 else "v")):
                        continue
                cost = gc + 1 + (self.TURN if nd != d else 0) + (self.CROSS if self.points.get(q) is not None and q not in goals else 0)
                s = (q, nd)
                if cost < best.get(s, 1e18):
                    best[s] = cost
                    prev[s] = (p, d)
                    heapq.heappush(openq, (cost + h(q), cost, q, nd))
        return None

    def _ann(self, allow):
        return all(self.G.kind(self.G.parts[a]) in ("junction", "nc") for a in allow if a in self.G.parts)


def snap_dn(v):
    return int(math.floor(v / GRID) * GRID)


def snap_up(v):
    return int(math.ceil(v / GRID) * GRID)


def corners(path):
    """Grid points -> polyline corners."""
    if len(path) < 3:
        return list(path)
    out = [path[0]]
    for i in range(1, len(path) - 1):
        a, b, c = out[-1], path[i], path[i + 1]
        if (a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1]):
            continue
        out.append(b)
    out.append(path[-1])
    return out


def _new_id(d, prefix):
    ids = {p["id"] for p in d["parts"]}
    i = 1
    while f"{prefix}{i}" in ids:
        i += 1
    return f"{prefix}{i}"


def route_all(d: dict, packs: str = "", only_unrouted: bool = False) -> dict:
    """Route every wire (or only those without 'pts') so it never crosses a symbol or field.  May split wires and add
    junctions.  Returns a new circuit dict; d['unrouted'] lists wires the router had to give up on."""
    d = copy.deepcopy(d)
    R = Router(d, packs)
    G = R.G
    wires = d.get("wires", [])
    polys = {}
    todo = []
    for i, w in enumerate(wires):
        if only_unrouted and w.get("pts") is not None:
            p = wire_path(G, w)
            if p:
                polys[i] = p
                R.occupy(i, p)
                continue
        todo.append(i)

    def dist(i):
        a, b = G.pin(wires[i]["a"]), G.pin(wires[i]["b"])
        return abs(a[0] - b[0]) + abs(a[1] - b[1]) if a and b else 0

    todo.sort(key=dist)
    failed = []
    for i in todo:
        w = wires[i]
        A, B = G.pin(w["a"]), G.pin(w["b"])
        if not A or not B:
            continue

        def term(ep, P):
            pid = ep.split(".")[0]
            k = G.kind(G.parts[pid])
            dirs = [(P[2] * GRID, P[3] * GRID)] if (P[2] or P[3]) else [(GRID, 0), (-GRID, 0), (0, GRID), (0, -GRID)]
            out = [((P[0], P[1]), dirs)]
            # branch points: any grid point of a wire already on this endpoint (KiCad T-junction)
            for j, pj in polys.items():
                if ep in (wires[j]["a"], wires[j]["b"]):
                    for q in grid_points(pj)[1:-1]:
                        if q in R.pin_tips or (R.blocked_by.get(q) and not (R.blocked_by[q] <= {pid} and k in ("junction", "nc"))):
                            continue
                        hz = any(pj[k2][1] == pj[k2 + 1][1] == q[1] and min(pj[k2][0], pj[k2 + 1][0]) <= q[0] <= max(pj[k2][0], pj[k2 + 1][0]) for k2 in range(len(pj) - 1))
                        out.append((q, [(0, GRID), (0, -GRID)] if hz else [(GRID, 0), (-GRID, 0)]))
            return out, (pid if k in ("junction", "nc") else None)

        src, pa = term(w["a"], A)
        dst, pb = term(w["b"], B)
        dst = [(p, [(dx, dy) for dx, dy in dirs]) for p, dirs in dst]
        allow = [x for x in (pa, pb) if x]
        # free the targets' own points from the crossing rules
        path = R.route(src, dst, allow)
        if not path:
            b = R.bounds
            R.bounds = (b[0] - 400, b[1] - 400, b[2] + 400, b[3] + 400)
            path = R.route(src, dst, allow)
            R.bounds = b
        if not path:
            failed.append(i)
            continue
        s, e = path[0], path[-1]
        a_ep, b_ep = w["a"], w["b"]
        if s != (A[0], A[1]):
            a_ep = _split_at(d, polys, R, G, w["a"], s)
        if e != (B[0], B[1]):
            b_ep = _split_at(d, polys, R, G, w["b"], e)
        poly = corners(path)
        w["a"], w["b"] = a_ep, b_ep
        w["pts"] = [{"x": x, "y": y} for x, y in poly[1:-1]]
        polys[i] = poly
        R.occupy(i, poly)
    for i in failed:
        wires[i].pop("pts", None)
    d["unrouted"] = [f"{wires[i]['a']}-{wires[i]['b']}" for i in failed]
    if not d["unrouted"]:
        d.pop("unrouted")
    return d


def _split_at(d, polys, R, G, ep, q):
    """Put a junction at grid point q on the wire of endpoint ep that passes through q; split that wire."""
    wires = d["wires"]
    for j, pj in list(polys.items()):
        w = wires[j]
        if ep not in (w["a"], w["b"]) or q not in grid_points(pj):
            continue
        jid = _new_id(d, "JN")
        d["parts"].append({"id": jid, "type": "junction", "params": {}, "x": q[0], "y": q[1], "rot": 0})
        G.parts[jid] = d["parts"][-1]
        # split polyline at q
        pts = grid_points(pj)
        k = pts.index(q)
        p1, p2 = corners(pts[:k + 1]), corners(pts[k:])
        R.release(j, pj)
        other = w["b"]
        w["b"] = f"{jid}.1"
        w["pts"] = [{"x": x, "y": y} for x, y in p1[1:-1]]
        polys[j] = p1
        R.occupy(j, p1)
        wires.append({"a": f"{jid}.1", "b": other, "pts": [{"x": x, "y": y} for x, y in p2[1:-1]]})
        polys[len(wires) - 1] = p2
        R.occupy(len(wires) - 1, p2)
        return f"{jid}.1"
    return ep


# ---------------------------------------------------------------- layout clean-up (power symbols at their pin, no overlaps)
def _rects(G, pt, m=0):
    b = G.box(pt)
    out = [[b[0] - m, b[1] - m, b[2] + m, b[3] + m]]
    f = G.fields(pt)
    if f:
        r = f["rect"]
        out.append([r[0] - m, r[1] - m, r[2] + m, r[3] + m])
    return out


def _hit(r1, r2):
    return r1[0] < r2[2] and r2[0] < r1[2] and r1[1] < r2[3] and r2[1] < r1[3]


def _stubs(G, pt, ln=20):
    """Keep-out lanes in front of every pin end (so parts do not sit on another part's wire exit): [(rect, ep)]."""
    out = []
    for ep in G.pins_of(pt):
        p = G.pin(ep)
        if p and (p[2] or p[3]):
            x2, y2 = p[0] + p[2] * ln, p[1] + p[3] * ln
            out.append(([min(p[0], x2) - 6, min(p[1], y2) - 6, max(p[0], x2) + 6, max(p[1], y2) + 6], ep))
    return out


_ROT_FOR = {}


def _pwr_rot(kind, dx, dy):
    """Rotation that makes a power symbol's pin point along (-dx,-dy) (towards the pin it serves)."""
    want = (-dx, -dy)
    base = (0, 1) if kind == "pwr" else (0, -1)
    for r in (0, 90, 180, 270):
        if xd({"rot": r}, *base) == want:
            return r
    return 0


def tidy(d: dict, movable=None, packs: str = "") -> dict:
    """KiCad-style clean layout for the given parts (default: everything but the MCU): one power symbol per
    connection placed one pin-length (100 mil) off the pin it feeds and facing it, other parts moved to the
    nearest free spot; then every wire is re-routed."""
    d = copy.deepcopy(d)
    parts = d["parts"]
    if movable is None:
        movable = {p["id"] for p in parts if p.get("type") != "mcu"}
    movable = set(movable)
    # 0. dissolve movable junctions (the router re-creates them where branches really meet)
    for pt in [p for p in parts if p["id"] in movable and p.get("type") == "junction"]:
        jep = f"{pt['id']}.1"
        others = [w["b"] if w["a"] == jep else w["a"] for w in d["wires"] if jep in (w["a"], w["b"])]
        d["wires"] = [w for w in d["wires"] if jep not in (w["a"], w["b"])]
        hub = next((o for o in others if o != jep), None)
        for o in others:
            if o != hub and o != jep:
                d["wires"].append({"a": hub, "b": o})
        parts.remove(pt)
    G = Geo(d, packs)
    # 1. one power symbol per wire
    for pt in list(parts):
        if pt["id"] not in movable or G.kind(pt) not in ("pwr", "gnd"):
            continue
        ws = [w for w in d["wires"] if f"{pt['id']}.1" in (w["a"], w["b"])]
        for w in ws[1:]:
            nid = _new_id(d, "PWR")
            parts.append({**copy.deepcopy(pt), "id": nid})
            movable.add(nid)
            for k in ("a", "b"):
                if w[k] == f"{pt['id']}.1":
                    w[k] = f"{nid}.1"
    G = Geo(d, packs)
    for w in d["wires"]:
        if w["a"].split(".")[0] in movable or w["b"].split(".")[0] in movable or w.get("pts") is None:
            w.pop("pts", None)
    fixed = [p for p in parts if p["id"] not in movable]
    occ = []
    for p in fixed:
        occ += [(r, p["id"]) for r in _rects(G, p, 10)] + _stubs(G, p, 60)

    def fits(pt, skip=None):
        rs = _rects(G, pt, 2 if G.kind(pt) in ("pwr", "gnd") else 10) + [r for r, _ in _stubs(G, pt, 20 if G.kind(pt) in ("pwr", "gnd") else 60)]
        bad = [own for a in rs for b, own in occ if own not in (skip or ()) and _hit(a, b)]
        return not bad

    def commit(pt):
        occ.extend([(r, pt["id"]) for r in _rects(G, pt, 2 if G.kind(pt) in ("pwr", "gnd") else 10)] + _stubs(G, pt, 20 if G.kind(pt) in ("pwr", "gnd") else 60))

    def spiral(pt):
        x0, y0 = snap(pt.get("x", 0)), snap(pt.get("y", 0))
        for r in range(0, 1200, 20):
            cand = [(x0 + dx, y0 + dy) for dx in range(-r, r + 1, 20) for dy in (-r, r)] + \
                   [(x0 + dx, y0 + dy) for dx in (-r, r) for dy in range(-r + 20, r, 20)]
            cand.sort(key=lambda c: abs(c[0] - x0) + abs(c[1] - y0))
            for x, y in cand:
                pt["x"], pt["y"] = x, y
                if fits(pt):
                    return
        pt["x"], pt["y"] = x0, y0

    # 1b. a passive wired straight to an MCU power pin that already has its own power symbol gets its own symbol
    #     (KiCad habit: decoupling caps drawn next to the chip, joined by net name, not by a long wire)
    psym, anchor = {}, {}
    for w in d["wires"]:
        for x, y in ((w["a"], w["b"]), (w["b"], w["a"])):
            q = G.parts.get(y.split(".")[0])
            if q and G.kind(q) in ("pwr", "gnd") and x.split(".")[0] not in movable:
                psym.setdefault(x, q)
    for w in d["wires"]:
        for k, o in (("a", "b"), ("b", "a")):
            q = G.parts.get(w[o].split(".")[0])
            if w[k] in psym and q and q["id"] in movable and G.kind(q) not in ("pwr", "gnd", "junction", "nc", "label", "glabel") \
                    and len(G.pins_of(q)) == 2:
                src = psym[w[k]]
                anchor.setdefault(q["id"], w[k])
                nid = _new_id(d, "PWR")
                parts.append({"id": nid, "type": src["type"], "params": {}, "x": q.get("x", 0), "y": q.get("y", 0)})
                movable.add(nid)
                w[k] = f"{nid}.1"
    G = Geo(d, packs)
    others = [p for p in parts if p["id"] in movable and G.kind(p) not in ("pwr", "gnd")]
    pwrs = [p for p in parts if p["id"] in movable and G.kind(p) in ("pwr", "gnd")]
    nbr = {}
    for w in d["wires"]:
        nbr.setdefault(w["a"].split(".")[0], []).append(w["b"])
        nbr.setdefault(w["b"].split(".")[0], []).append(w["a"])

    def place_pwr(pt):
        tgt = (nbr.get(pt["id"]) or [None])[0]
        P = G.pin(tgt) if tgt else None
        k = G.kind(pt)
        if P and (P[2] or P[3]):
            for dist, off in [(20, 0), (40, 0), (60, 0)] + [(dd, o) for dd in (80, 100, 120, 140, 160, 200) for o in (0, 20, -20, 40, -40, 60, -60, 80, -80)]:
                pt["rot"] = _pwr_rot(k, P[2], P[3])
                pt.pop("mx", None), pt.pop("my", None)
                pt["x"], pt["y"] = P[0] + P[2] * dist + P[3] * off, P[1] + P[3] * dist + P[2] * off
                if fits(pt, (tgt, tgt.split(".")[0])):
                    commit(pt)
                    return True
        return False

    # parts first in their requested spot (or nearest free), each followed by its own power symbols
    nbr_ep, pdone = {}, set()
    for w in d["wires"]:
        nbr_ep.setdefault(w["a"], []).append(w["b"])
        nbr_ep.setdefault(w["b"], []).append(w["a"])
    for pt in pwrs:      # MCU / fixed-part power symbols first
        t = (nbr.get(pt["id"]) or ["?"])[0].split(".")[0]
        if t not in movable and place_pwr(pt):
            pdone.add(pt["id"])
    # BFS from the fixed parts: each part goes next to the pin it hangs on
    placed = {p["id"] for p in fixed}
    left = list(others)
    while left:
        best = None
        for pt in left:
            for ep in G.pins_of(pt):
                for w in d["wires"]:
                    o = w["b"] if w["a"] == ep else w["a"] if w["b"] == ep else None
                    if o and o.split(".")[0] in placed:
                        P = G.pin(o)
                        if P and (P[2] or P[3]):
                            best = (pt, ep, P)
                            break
                if best:
                    break
            if best:
                break
        if not best:
            for pt in left:
                if pt["id"] in anchor and G.pin(anchor[pt["id"]]):
                    ep0 = anchor[pt["id"]]
                    P = G.pin(ep0)
                    best = (pt, None, P)
                    break
        if best and best[1] is None:
            pt, _, P = best
            pt["rot"] = 0 if P[3] else 90
            b = G.box({**pt, "x": 0, "y": 0})
            ext = max(abs(b[1]), abs(b[3])) if P[3] else max(abs(b[0]), abs(b[2]))
            pt["x"], pt["y"] = snap(P[0] + P[2] * (70 + ext) + P[3] * 40), snap(P[1] + P[3] * (70 + ext) + P[2] * 40)
        elif not best:
            pt = left[0]
            pt["x"], pt["y"] = snap(pt.get("x", 0)), snap(pt.get("y", 0))
        else:
            pt, ep, P = best
            # orient a 2-pin part along the pin direction, own pin facing it
            if G.kind(pt) not in ("label", "glabel", "dip", "module", "conn") or len(G.pins_of(pt)) <= 2:
                ok = False
                for mx in (False, True):
                    for r in (0, 90, 180, 270):
                        pt["rot"] = r
                        pt.pop("my", None)
                        if mx:
                            pt["mx"] = True
                        else:
                            pt.pop("mx", None)
                        Q = G.pin(ep)
                        if (Q[2], Q[3]) == (-P[2], -P[3]):
                            ok = True
                            break
                    if ok:
                        break
            pt["x"], pt["y"] = 0, 0
            Q = G.pin(ep)
            n = len(G.pins_of(pt))
            gap = 60 if n <= 3 else min(260, 100 + 12 * n)
            pt["x"], pt["y"] = P[0] + P[2] * gap - Q[0], P[1] + P[3] * gap - Q[1]
        spiral(pt)
        commit(pt)
        placed.add(pt["id"])
        left.remove(pt)
        for ep in G.pins_of(pt):
            for o in list(nbr_ep.get(ep, [])):
                q = G.parts.get(o.split(".")[0])
                if q in pwrs and q["id"] not in pdone and place_pwr(q):
                    pdone.add(q["id"])
    # power symbols of fixed parts first (MCU pins), then the rest
    for pt in pwrs:
        if pt["id"] in pdone:
            continue
        if not place_pwr(pt):
            pt["rot"] = _pwr_rot(G.kind(pt), 0, 1 if G.kind(pt) == "gnd" else -1)
            spiral(pt)
            commit(pt)
    return route_all(d, packs, only_unrouted=True)


# ---------------------------------------------------------------- geometric verification (used by the tests and the ERC)
def audit(d: dict, packs: str = "") -> dict:
    """Checks the drawing like KiCad would read it: connectivity only from coordinates.
    Returns {problems:[...], dangling:[ep], unconnected_pins:[ep]}."""
    G = Geo(d, packs)
    probs = []
    polys = []
    for w in d.get("wires", []):
        pl = wire_path(G, w)
        if not pl:
            probs.append(f"wire {w['a']}-{w['b']}: endpoint missing")
            continue
        polys.append((w, pl))
        for x, y in pl:
            if x % GRID or y % GRID:
                probs.append(f"wire {w['a']}-{w['b']}: off-grid point {x},{y}")
        for (x0, y0), (x1, y1) in zip(pl, pl[1:]):
            if x0 != x1 and y0 != y1:
                probs.append(f"wire {w['a']}-{w['b']}: diagonal segment")
    # bodies: no wire segment may enter a symbol box (pin ends sit on the edge)
    for w, pl in polys:
        for pt in d["parts"]:
            if G.kind(pt) in ("junction", "nc"):
                continue
            b = G.box(pt)
            for (x0, y0), (x1, y1) in zip(pl, pl[1:]):
                lx, hx, ly, hy = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
                if lx < b[2] and hx > b[0] and ly < b[3] and hy > b[1]:
                    if (lx == hx and b[0] < lx < b[2]) or (ly == hy and b[1] < ly < b[3]):
                        probs.append(f"wire {w['a']}-{w['b']} crosses {pt['id']}")
                        break
            f = G.fields(pt)
            if f:
                r = f["rect"]
                for (x0, y0), (x1, y1) in zip(pl, pl[1:]):
                    lx, hx, ly, hy = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
                    if lx <= r[2] and hx >= r[0] and ly <= r[3] and hy >= r[1]:
                        probs.append(f"wire {w['a']}-{w['b']} crosses the text of {pt['id']}")
                        break
    # collinear overlaps
    seen = {}
    for i, (w, pl) in enumerate(polys):
        for e in unit_edges(pl):
            if e in seen and seen[e] != i:
                probs.append(f"wires overlap at {e[1]},{e[2]}")
            seen[e] = i
    # geometric connectivity: union at pin ends, wire ends, junctions; T-joins only via a junction
    par = {}

    def f(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    def u(a, b):
        par[f(a)] = f(b)

    tips = {}
    for pt in d["parts"]:
        for ep in G.pins_of(pt):
            p = G.pin(ep)
            if p:
                tips.setdefault((p[0], p[1]), []).append(ep)
                u(("ep", ep), ("pt", p[0], p[1]))
    for w, pl in polys:
        u(("pt",) + pl[0], ("pt",) + pl[-1])
        for q in grid_points(pl)[1:-1]:
            if q in tips:
                probs.append(f"wire {w['a']}-{w['b']} runs over pin end {tips[q]}")
    ends = {}
    for w, pl in polys:
        for q in (pl[0], pl[-1]):
            ends[q] = ends.get(q, 0) + 1
    for w, pl in polys:
        mids = set(grid_points(pl)[1:-1])
        for q in ends:
            if q in mids:
                probs.append(f"a wire end touches the middle of wire {w['a']}-{w['b']} at {q} (needs a junction)")
    # compare with the schema netlist (a/b graph)
    par2 = {}

    def f2(x):
        par2.setdefault(x, x)
        while par2[x] != x:
            par2[x] = par2[par2[x]]
            x = par2[x]
        return x

    for w in d.get("wires", []):
        par2[f2(w["a"])] = f2(w["b"])
    allep = [ep for pt in d["parts"] for ep in G.pins_of(pt)]
    g1, g2 = {}, {}
    for ep in allep:
        g1.setdefault(f(("ep", ep)), set()).add(ep)
        g2.setdefault(f2(ep), set()).add(ep)
    s1 = {frozenset(v) for v in g1.values() if len(v) > 1}
    s2 = {frozenset(v) for v in g2.values() if len(v) > 1}
    for v in s1 ^ s2:
        probs.append(("geometry net " if v in s1 else "schema net ") + ",".join(sorted(v)) + " differs")
    used = {e for w in d.get("wires", []) for e in (w["a"], w["b"])}
    dangling = [f"{pt['id']}.1" for pt in d["parts"] if G.kind(pt) == "junction" and
                sum(1 for w in d.get("wires", []) for e in (w["a"], w["b"]) if e == f"{pt['id']}.1") < 2]
    uncon = [ep for pt in d["parts"] if G.kind(pt) not in ("junction",) for ep in G.pins_of(pt)
             if ep not in used and pt.get("type") != "mcu" and G.kind(pt) != "nc"]
    return {"problems": probs, "dangling": dangling, "unconnected_pins": uncon}
