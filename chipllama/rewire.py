"""M9.2 Auto re-wire: remap every MCU connection onto valid pins of the CURRENT device, rebuild the MCU support
circuitry (decoupling / VCAP / MCLR / ICSP on bare PICs, nothing on dev boards), re-place all parts around the MCU and
re-route every wire with the strict router.  Pure function: returns a new circuit + a summary."""
from __future__ import annotations

import copy
import re

from . import components as C
from . import geometry as GM
from .circuit import Circuit, check
from .geometry import _pwr_rot
from .schematic import _Auto, _auto_mcu, migrate

SUPPORT_T = {"capacitor", "ecap", "resistor", "icsp", "noconnect"}
PAIR = {"scl": "sda", "sda": "scl", "rx": "tx", "tx": "rx", "sck": "mosi", "mosi": "sck", "miso": "sck"}


BUS_FN = {"rx": r"U(\d)TX|TX(\d)?", "tx": r"U(\d)RX|RX(\d)?", "scl": r"SCL(\d)?", "sda": r"SDA(\d)?",
          "sck": r"SCK(\d)?", "mosi": r"SDO(\d)?", "miso": r"SDI(\d)?"}
BUS_OF = {"rx": "uart", "tx": "uart", "scl": "i2c", "sda": "i2c", "sck": "spi", "mosi": "spi", "miso": "spi"}


def _mods(A: _Auto, num: str, role: str) -> dict:
    """{module digit: function name} the pin offers for this role ({'*': None} = capable, no module notion)."""
    p = A.c.pins.get(num)
    if not p or p.get("power") or not p.get("port") or num in A.reserved:
        return {}
    note = str(p.get("note") or "").lower()
    if "do not use" in note or ("input only" in note and role not in ("in", "an", "rx")):
        return {}
    v = A.c.valid_functions(num)
    if role in BUS_FN:
        out = {}
        for f in v:
            m = re.fullmatch(BUS_FN[role], f)
            if m:
                out.setdefault(next((g for g in m.groups() if g), "0"), f)
        return out
    if role == "an":
        return {"*": None} if any(f.startswith("AN") for f in v) else {}
    if role == "pwm":
        return {"*": None} if any(re.fullmatch(r"OC\d[A-C]?|CCP\d|PWM\d[HL]?|TIM\d+_CH\d|LEDC", f) for f in v) else {}
    return {"*": None}


def _pick(A: _Auto, role: str, allowed: set | None):
    cands = []
    for num, p in A.c.pins.items():
        if num in A.used:
            continue
        m = _mods(A, num, role)
        if not m or (allowed is not None and not set(m) & allowed):
            continue
        v = A.c.valid_functions(num)
        score = len(v) if role not in BUS_FN and role not in ("an", "pwm") else 0
        if role not in BUS_FN and role != "an" and any(f.startswith("AN") for f in v):
            score += 20
        if role == "in" and A.c.weak_pullup_ok(num):
            score -= 50
        cands.append((score, int(num) if num.isdigit() else 999, num))
    return min(cands)[2] if cands else None


def _roles(d, packs):
    """pin number -> role on the device the drawing was made for (from the checker's bindings)."""
    out = {}
    try:
        for b in Circuit(copy.deepcopy(d), packs).run()["bindings"]:
            for pb in b["pins"].values():
                out[str(pb["pin"])] = pb["role"]
    except Exception:
        pass
    return out


def _power_pins(d, packs):
    try:
        c = Circuit(copy.deepcopy(d), packs)
        return c.mcu, {n for n, p in c.pins.items() if p.get("power")}, {n for n, p in c.pins.items()
                       if any(re.fullmatch(r"PG[EC]?[DC]\d|PGE[DC]\d|MCLR", s.upper()) for s in p["names"])}
    except Exception:
        return "U1", set(), set()


def rewire(d: dict, packs: str = "", device: str | None = None) -> dict:
    """Best of a few attempts: a pin whose wire cannot be routed (nor labelled) is banned and the remap redone."""
    ban: set = set()
    best = None
    for _ in range(4):
        r = _rewire(d, packs, device, ban)
        s = r["summary"]
        score = s["overlaps"] + 10 * s["unrouted"] + 5 * s["erc_errors"]
        if best is None or score < best[0]:
            best = (score, r)
        if not score:
            break
        mcu = r["circuit"]["parts"] and next((p["id"] for p in r["circuit"]["parts"] if p.get("type") == "mcu"), "U1")
        bad = set()
        for u in (r["circuit"].get("unrouted") or []) + [f"{a}-{b}" for pr in r["audit"]["problems"]
                                                          for a, b in _WIRE.findall(pr)]:
            for ep in u.split("-"):
                if ep.startswith(mcu + "."):
                    bad.add(ep.split(".", 1)[1])
        for f in r["check"]["findings"]:
            if f.get("severity") == "error":
                bad.update(re.findall(r"\(pin (\w+)\)", f.get("message", "")))
        new_ban = bad
        if not new_ban - ban:
            break
        ban |= new_ban
    out = best[1]
    out.pop("pinmap", None)
    return out


def _rewire(d, packs, device, ban):
    old = migrate(copy.deepcopy(d))
    roles = _roles(old, packs)
    mcu, opower, ospecial = _power_pins(old, packs)
    new = copy.deepcopy(old)
    if device:
        new["device"] = device
    new["assign"] = {}
    from . import families as FAM
    fam = FAM.board((new.get("device") or "").upper())
    if fam:
        new["family"] = fam.get("family") or new.get("family")
        new["power"] = "board"
    else:
        new.pop("family", None)
    for jn in [p for p in new["parts"] if p.get("type") == "junction"]:      # junctions are re-made by the router
        jep = f"{jn['id']}.1"
        others = [w["b"] if w["a"] == jep else w["a"] for w in new["wires"] if jep in (w["a"], w["b"])]
        others = [o for o in others if o != jep]
        new["wires"] = [w for w in new["wires"] if jep not in (w["a"], w["b"])]
        for o in others[1:]:
            new["wires"].append({"a": others[0], "b": o})
        new["parts"].remove(jn)
    parts = {p["id"]: p for p in new["parts"]}
    rails = {pid for pid, p in parts.items() if p.get("type") in C.POWER_SYM}
    nb = {}
    for w in new["wires"]:
        nb.setdefault(w["a"].split(".")[0], []).append(w["b"])
        nb.setdefault(w["b"].split(".")[0], []).append(w["a"])
    # 1. MCU support circuitry (anything that only talks to MCU power/MCLR/PGx pins, rails or other support parts)
    sup = {pid for pid, p in parts.items() if p.get("type") in SUPPORT_T}
    changed = True
    while changed:
        changed = False
        for pid in list(sup):
            for ep in nb.get(pid, []):
                o, _, port = ep.partition(".")
                if o == mcu:
                    if port in opower or port in ospecial or parts[pid]["type"] == "icsp":
                        continue
                elif o in rails or o in sup or o == pid:
                    continue
                sup.discard(pid); changed = True
                break
    sup = {pid for pid in sup if any(ep.split(".")[0] == mcu for ep in nb.get(pid, []))
           or parts[pid]["type"] == "capacitor" and nb.get(pid) and all(e.split(".")[0] in rails for e in nb[pid])
           or parts[pid]["type"] in ("noconnect", "resistor") and any(e.split(".")[0] in sup for e in nb.get(pid, []))}
    removed = sorted(sup)
    new["wires"] = [w for w in new["wires"] if w["a"].split(".")[0] not in sup and w["b"].split(".")[0] not in sup
                    and not any(e.split(".")[0] == mcu and e.split(".")[1] in opower for e in (w["a"], w["b"]))]
    new["parts"] = [p for p in new["parts"] if p["id"] not in sup]
    used_eps = {e for w in new["wires"] for e in (w["a"], w["b"])}
    new["parts"] = [p for p in new["parts"] if not (p.get("type") in C.POWER_SYM and f"{p['id']}.1" not in used_eps)]
    for w in new["wires"]:
        w.pop("pts", None)
    # 2. remap MCU signal pins
    A = _Auto(new, packs)
    A.used = set()
    mp, remapped, dropped = {}, [], []
    order = sorted({e.split(".")[1] for w in new["wires"] for e in (w["a"], w["b"]) if e.split(".")[0] == A.mcu},
                   key=lambda n: (roles.get(n, "io") in ("out", "in", "io", "cs"), n))
    peers = {}   # old pin -> old pins of the same bus wired to the same part (they must share a module)
    touch = {}
    for w in new["wires"]:
        for x, y in ((w["a"], w["b"]), (w["b"], w["a"])):
            if x.split(".")[0] == A.mcu:
                touch.setdefault(x.split(".")[1], set()).add(y.split(".")[0])
    for n in order:
        bus = BUS_OF.get(roles.get(n, "io"))
        peers[n] = {k for k in order if k != n and bus and BUS_OF.get(roles.get(k, "io")) == bus
                    and touch.get(k, set()) & touch.get(n, set())}
    module = {}
    for n in order:
        role = roles.get(n, "io")
        allowed = None
        if role in BUS_FN:
            got = [module[k] for k in peers[n] if k in module]
            if got:
                allowed = {got[0]}
        m = {} if (n in A.used or n in ban) else _mods(A, n, role)
        if m and (allowed is None or set(m) & allowed):
            tgt = n
        else:
            A.used |= set(ban)
            tgt = role in ("out", "in", "io", "cs") and A.free_pin(role, prefer_cn=role == "in") or _pick(A, role, allowed)
            if tgt is None:
                dropped.append(n)
            else:
                remapped.append((n, tgt))
        if tgt:
            A.used.add(tgt)
            if role in BUS_FN:
                mm = _mods(A, tgt, role)
                k = sorted(set(mm) & allowed)[0] if allowed else sorted(mm)[0]
                module[n] = k
                for pn in peers[n]:
                    module.setdefault(pn, k)
                new["assign"][tgt] = mm[k]
        mp[n] = tgt
    ws = []
    for w in new["wires"]:
        ok = True
        for k in ("a", "b"):
            pid, _, port = w[k].partition(".")
            if pid == A.mcu:
                if mp.get(port) is None:
                    ok = False
                else:
                    w[k] = f"{pid}.{mp[port]}"
        if ok:
            ws.append(w)
    new["wires"] = ws
    A = _Auto(new, packs)
    # 3. bare MCU -> rebuild decoupling / VCAP / MCLR / ICSP;  dev board -> the board does it
    if not fam:
        mx = next(p for p in new["parts"] if p["id"] == A.mcu)
        _auto_mcu(A, float(mx.get("x", 0)), float(mx.get("y", 0)))
        new = A.d
    new["schema"] = 2
    # 4. place everything around the MCU and route with the strict router (labels as fallback)
    def bad(x, a):
        return len(a["problems"]) + len(a["dangling"]) + 10 * len(x.get("unrouted") or [])
    best = None
    variants = [lambda ps: ps, lambda ps: ps[:1] + ps[1:][::-1],
                lambda ps: sorted(ps, key=lambda p: (p.get("type") != "mcu", -len(p.get("id", ""))))]
    for i, f in enumerate(variants + ["tidy"]):
        x = copy.deepcopy(new)
        if f == "tidy":
            o = GM.tidy(x, None, packs)
        else:
            mcu_first = sorted(x["parts"], key=lambda p: p.get("type") != "mcu")
            x["parts"] = f(mcu_first)
            o = GM._tidy(x, None, packs)
        o, a_ = repair(o, packs, GM.audit(o, packs))
        if best is None or bad(o, a_) < best[0]:
            best = (bad(o, a_), o, a_)
        if not best[0]:
            break
    _, out, au = best
    res = check(out, packs)
    adjusted = []
    for f in res["findings"]:      # 5 V logic part on a 3.3 V-only device: switch its I/O level (jumper on real adapters)
        m = re.match(r"(\w+)\.\w+ drives 5 V into", f.get("message", ""))
        pt = m and next((p for p in out["parts"] if p["id"] == m.group(1)), None)
        if f.get("severity") == "error" and pt and float((pt.get("params") or {}).get("io_v", 0) or 0) >= 5:
            pt["params"]["io_v"] = 3.3
            adjusted.append(f"{pt['id']} I/O level set to 3.3 V")
    if adjusted:
        res = check(out, packs)
    labels = sum(1 for p in out["parts"] if p.get("type") in ("label", "glabel"))
    lab = lambda n: (A.c.label(n) if n in A.c.pins else n)
    summary = {"device": out.get("device"), "wires": len(out["wires"]), "labels": labels,
               "remapped": [{"from": a, "to": b, "to_label": lab(b)} for a, b in remapped], "dropped": dropped,
               "removed_support": removed, "adjusted": sorted(set(adjusted)), "overlaps": len(au["problems"]), "unrouted": len(out.get("unrouted") or []),
               "erc_errors": sum(1 for f in res["findings"] if f.get("severity") == "error")}
    return {"circuit": out, "summary": summary, "audit": au, "pinmap": mp, "check": {"ok": res["ok"], "findings": res["findings"]}}


_WIRE = re.compile(r"wire ([\w.+-]+?\.[\w+-]+)-([\w+-]+\.[\w+-]+)")


_AT = re.compile(r"at (-?[\d.]+),(-?[\d.]+)")


def _through(pts, x, y):
    pts = [(float(p["x"]), float(p["y"])) for p in pts]
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        if min(ax, bx) - .5 <= x <= max(ax, bx) + .5 and min(ay, by) - .5 <= y <= max(ay, by) + .5:
            return True
    return False


def _score(a):
    return len(a["problems"]) + len(a["dangling"])


def _move_pwr(d, packs, au):
    """A power/GND symbol whose wire could not be routed is local: try it at free spots right next to its pin."""
    for _ in range(3):
        n0 = len(d.get("unrouted") or [])
        d, au = _move_pwr1(d, packs, au)
        if not d.get("unrouted") or len(d.get("unrouted") or []) >= n0:
            break
    return d, au


def _move_pwr1(d, packs, au):
    G = GM.Geo(d, packs)
    for u in list(d.get("unrouted") or []):
        a, _, b = u.partition("-")
        for tgt, sym in ((a, b), (b, a)):
            pt = G.parts.get(sym.split(".")[0])
            P = G.pin(tgt)
            if not pt or not P or G.kind(pt) not in ("pwr", "gnd") or not (P[2] or P[3]):
                continue
            k = G.kind(pt)
            tries = [(0, 1 if k == "gnd" else -1, dist, dy) for dist in (20, 30, 40) for dy in (30, 40, 60)] if P[2] and not P[3] else []
            tries += [(P[2], P[3], dd, o) for dd in (20, 40, 60, 80) for o in (0, 20, -20, 40, -40, 60, -60, 80, -80)]
            tot = lambda x_, a_: _score(a_) + 10 * len(x_.get("unrouted") or [])
            best = (tot(d, au), d, au)
            for dx, dy, dist, off in tries:
                x = copy.deepcopy(d)
                q = next(p for p in x["parts"] if p["id"] == pt["id"])
                q.pop("mx", None), q.pop("my", None)
                q["rot"] = _pwr_rot(k, dx, dy)
                if dx == 0 and dy and P[2] and not P[3] and (dx, dy) != (P[2], P[3]):
                    q["x"], q["y"] = P[0] + P[2] * dist, P[1] + dy * off
                else:
                    q["x"], q["y"] = P[0] + P[2] * dist + P[3] * off, P[1] + P[3] * dist + P[2] * off
                for w in x["wires"]:
                    if {w["a"], w["b"]} == {a, b}:
                        w.pop("pts", None)
                x.pop("unrouted", None)
                x = GM.route_all(x, packs, only_unrouted=True)
                a2 = GM.audit(x, packs)
                if tot(x, a2) < best[0]:
                    best = (tot(x, a2), x, a2)
                    if not best[0]:
                        break
            d, au = best[1], best[2]
            G = GM.Geo(d, packs)
            break
    return d, au


def repair(d: dict, packs: str = "", au: dict | None = None, rounds: int = 4):
    """Re-route only the wires the validator complains about (the rest stay as obstacles); the router then goes
    around everything or falls back to a net-label pair.  Never returns something worse than its input."""
    au = au or GM.audit(d, packs)
    if d.get("unrouted"):
        d, au = _move_pwr(d, packs, au)
    best, best_au = d, au
    cur = d
    for _ in range(rounds):
        if not best_au["problems"] and not best_au["dangling"]:
            break
        bad = set()
        for pr in best_au["problems"]:
            bad.update(_WIRE.findall(pr))
            for x, y in _AT.findall(pr):      # "wires overlap at x,y": every wire through that point
                bad.update((w["a"], w["b"]) for w in best["wires"] if _through(w.get("pts") or [], float(x), float(y)))
        if not bad:
            break
        cur = copy.deepcopy(best)
        for w in cur["wires"]:
            if (w["a"], w["b"]) in bad or (w["b"], w["a"]) in bad:
                w.pop("pts", None)
        cur = GM.route_all(cur, packs, only_unrouted=True)
        a2 = GM.audit(cur, packs)
        if len(a2["problems"]) + len(a2["dangling"]) < len(best_au["problems"]) + len(best_au["dangling"]):
            best, best_au = cur, a2
        else:
            break
    if best.get("unrouted"):
        best, best_au = _move_pwr(best, packs, best_au)
    return best, best_au
