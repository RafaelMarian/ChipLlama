"""Multimodal reverse debug: photo/screenshot -> STRICT JSON extraction (Muse Spark vision)
-> deterministic bit-level analysis (chipdb + timers + uart) -> grounded explanation + fix diff.

The model only *reads* the image and *explains*; every number in the findings table is computed here.
"""
from __future__ import annotations

import difflib
import json
import math
import re
from dataclasses import dataclass, field, asdict

from . import chipdb
from .models.base import ChatModel, Message, ModelError
from .timers import PRESCALERS, PRESCALE_TO_CODE, TimerSpec, parse_request, solve, timer_period_s
from .uart import baud_from_brg, solve_baud

KINDS = ("registers", "scope", "serial", "unknown")
COSC_NAMES = {0: "FRC (8 MHz)", 1: "FRCPLL (FRC + 96 MHz PLL)", 2: "Primary (XT/HS/EC)", 3: "Primary + PLL",
              4: "SOSC (32.768 kHz)", 5: "LPRC (31 kHz)", 6: "FRC/16", 7: "FRCDIV (FRC / RCDIV)"}
RCDIV_HZ = {0: 8e6, 1: 4e6, 2: 2e6, 3: 1e6, 4: 500e3, 5: 250e3, 6: 125e3, 7: 31.25e3}
PDSEL = {0: "8N", 1: "8E", 2: "8O", 3: "9N"}
COSC_FOR_PROFILE = {"frcpll": 1, "frc": 0, "frcdiv": 7, "pri": 2, "xt": 2, "hs": 2, "ec": 2, "pripll": 3, "xtpll": 3, "hspll": 3, "ecpll": 3}
PAGES = {"T1CON": 162, "T2CON": 166, "T3CON": 167, "OSCCON": 123, "CLKDIV": 125, "U1MODE": 202, "U1STA": 204,
         "U1BRG": 200, "PR1": 162, "PR2": 166, "PR3": 167, "scope": 161, "baud": 200}

EXTRACT_PROMPT = """You are the vision front-end of ChipLlama, a PIC24FJ256GB110 debugging tool.
Look at the attached image(s). They are ONE of:
 - "registers": an MPLAB X SFR / Watches / Variables window, or a register dump (name + hex value)
 - "scope": an oscilloscope or logic-analyzer screen showing a digital waveform
 - "serial": a serial terminal window (PuTTY, Tera Term, MPLAB Data Visualizer, ...)
Return ONLY one JSON object, no prose, no markdown fences, exactly this schema:
{"kind": "registers"|"scope"|"serial"|"unknown",
 "registers": {"T1CON": "0x8010", "PR1": "0x4E1F"},
 "scope": {"period_ms": number|null, "freq_hz": number|null, "duty": number|null (0..1), "voltage": number|null (peak volts),
           "v_high": number|null (logic-high level, volts), "v_low": number|null (logic-low level, volts),
           "vdd": number|null, "channel": string|null, "timebase": string|null},
 "serial": {"text": string|null, "baud_shown": number|null},
 "confidence": number (0..1),
 "notes": string}
Rules: copy register values exactly as shown, as hex strings; omit a register you cannot read; never guess.
For a scope, read the measurement panel if present (Period/Freq/+Duty/Vpp), otherwise estimate from divisions and timebase and lower confidence.
Always read the HIGH and LOW voltage levels of a digital trace (cursors, Max/Min/Top/Base readouts, or divisions x V/div from the ground marker).
For serial text, copy it verbatim including garbage characters.
Copy EVERY register/variable you can read (also LATx, PORTx, UxRXREG, RPORn, RPINRn), not only timer/UART ones."""

REPAIR_PROMPT = "Your previous reply was not valid JSON for the schema. Reply again with ONLY the JSON object, nothing else."

EXPLAIN_SYSTEM = """You are ChipLlama's reverse-debugging assistant for the PIC24FJ256GB110 (datasheet DS39897C), XC16 compiler.
Rows marked INFO are unrelated context: do not present them as the bug. If the facts say UNCERTAIN or NOT a bug, say exactly that.
You receive DETERMINISTIC findings computed by ChipLlama from registers/measurements read off the user's image. They are ground truth:
never contradict a decoded value or a computed number. Write:
### Root cause
2-4 sentences, plain language, exact REGISTER.BIT names, cite datasheet pages like (DS39897C p.162).
### Fix
One ```diff block (unified diff against the user's code if given, otherwise a minimal C snippet as + lines) using XC16 names (T1CONbits.TCKPS, PR1, U1BRG, #pragma config FNOSC ...).
### How to confirm
1-3 bullet points (what to re-measure). Under 220 words total."""


# ------------------------------------------------------------------ step 1: extraction + JSON repair
@dataclass
class Extraction:
    kind: str = "unknown"
    registers: dict[str, int] = field(default_factory=dict)
    other: dict[str, int] = field(default_factory=dict)   # register-like names not in the built-in table (LATA, U1RXREG ...)
    scope: dict = field(default_factory=dict)
    serial: dict = field(default_factory=dict)
    confidence: float = 0.0
    notes: str = ""
    repaired: list[str] = field(default_factory=list)   # what the repair step had to do
    raw: str = ""
    boxes: dict = field(default_factory=dict)           # optional name -> [x0,y0,x1,y1] (0..1) from the model
    candidates: dict[str, list[int]] = field(default_factory=dict)  # every distinct reading per register (multi-pass)
    disputed: dict[str, list[int]] = field(default_factory=dict)    # unresolved: passes disagree, no majority
    passes: list[dict] = field(default_factory=list)    # per-pass log for the dashboard

    def to_dict(self):
        d = asdict(self)
        d["registers"] = {k: f"0x{v:04X}" for k, v in {**self.other, **self.registers}.items()}
        d.pop("other", None)
        d["candidates"] = {k: [f"0x{x:04X}" for x in v] for k, v in self.candidates.items()}
        d["disputed"] = {k: [f"0x{x:04X}" for x in v] for k, v in self.disputed.items()}
        return d

    def all_regs(self) -> dict[str, int]:
        return {**self.other, **self.registers}

    def set_reg(self, name: str, val: int):
        (self.registers if name in self.registers else self.other)[name] = val


def _loose_json(text: str, fixes: list[str]) -> dict:
    """Parse model output into a dict, repairing the usual LLM JSON mistakes."""
    t = (text or "").strip()
    if not t:
        raise ValueError("empty reply")
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip(); fixes.append("stripped markdown fence")
    a, b = t.find("{"), t.rfind("}")
    if a < 0:
        raise ValueError("no JSON object in reply")
    if b < a:
        t = t[a:] + "}" * (t[a:].count("{") - t[a:].count("}")); fixes.append("closed truncated object")
    else:
        if a > 0 or b < len(t) - 1:
            fixes.append("removed text around JSON")
        t = t[a:b + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    s = re.sub(r"//[^\n]*", "", t)
    s = re.sub(r",\s*([}\]])", r"\1", s)
    s = re.sub(r"\bNone\b", "null", s); s = re.sub(r"\bTrue\b", "true", s); s = re.sub(r"\bFalse\b", "false", s)
    if "'" in s and '"' not in s:
        s = s.replace("'", '"')
    s = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', s)          # unquoted keys
    s = re.sub(r':\s*(0[xX][0-9A-Fa-f]+|[0-9A-Fa-f]{4}h)\b', r': "\1"', s)        # bare hex values
    open_b = s.count("{") - s.count("}")
    if open_b > 0:
        s += "}" * open_b
    fixes.append("repaired JSON syntax")
    return json.loads(s)


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", "."))
    if not m:
        return None
    x = float(m.group(0)); s = str(v).lower()
    for unit, k in (("µs", 1e-3), ("us", 1e-3), ("ms", 1.0), ("khz", 1e3), ("mhz", 1e6)):
        if unit in s:
            return x * k
    return x


def _hex(v) -> int | None:
    s = str(v).strip().replace("_", "").replace(" ", "")
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        if s.lower().startswith("0b"):
            return int(s[2:], 2)
        if s.lower().endswith("h"):
            return int(s[:-1], 16)
        if isinstance(v, int):
            return v
        return int(s, 16)
    except ValueError:
        return None


def normalize(j: dict, fixes: list[str] | None = None) -> Extraction:
    fixes = fixes if fixes is not None else []
    if not isinstance(j, dict):
        raise ValueError("JSON is not an object")
    ex = Extraction(repaired=fixes)
    regs_in = j.get("registers") or {}
    if isinstance(regs_in, list):                  # [{"name":..,"value":..}]
        regs_in = {str(r.get("name")): r.get("value") for r in regs_in if isinstance(r, dict)}
        fixes.append("converted register list to map")
    known = set(chipdb.CORE_NAMES)
    for k, v in (regs_in.items() if isinstance(regs_in, dict) else []):
        name = chipdb.REG_ALIASES.get(str(k).upper().strip(), str(k).upper().strip())
        val = _hex(v)
        if name in known and val is not None and 0 <= val <= 0xFFFF:
            ex.registers[name] = val
        elif val is not None and 0 <= val <= 0xFFFFFFFF and re.fullmatch(r"(LAT|PORT|TRIS|ODC|CN)[A-K]|U\dRXREG|U\dTXREG|RPOR\d+|RPINR\d+|ANS[A-K]|AD1PCFG[LH]?", name):
            ex.other[name] = val
        else:
            fixes.append(f"dropped {k}={v!r} (unknown register or bad value)")
    sc = j.get("scope") or {}
    if isinstance(sc, dict):
        per, fr = _num(sc.get("period_ms")), _num(sc.get("freq_hz"))
        if per is None and fr:
            per = 1e3 / fr; fixes.append("period derived from frequency")
        if fr is None and per:
            fr = 1e3 / per
        duty = _num(sc.get("duty"))
        if duty is not None and duty > 1:
            duty /= 100; fixes.append("duty given in % -> fraction")
        def first(*keys):
            for k in keys:
                v = _num(sc.get(k))
                if v is not None:
                    return v
            return None
        vh = first("v_high", "high_v", "v_top", "vtop", "v_max", "vmax", "high")
        vl = first("v_low", "low_v", "v_base", "vbase", "v_min", "vmin", "low")
        ex.scope = {k: v for k, v in {"period_ms": per, "freq_hz": fr, "duty": duty, "voltage": _num(sc.get("voltage")),
                                      "v_high": vh, "v_low": vl, "vdd": first("vdd", "logic_v"),
                                      "channel": sc.get("channel"), "timebase": sc.get("timebase")}.items() if v is not None}
    se = j.get("serial") or {}
    if isinstance(se, str):
        se = {"text": se}
    if isinstance(se, dict) and se.get("text"):
        ex.serial = {"text": str(se["text"])[:4000], "baud_shown": _num(se.get("baud_shown"))}
    kind = str(j.get("kind", "")).lower().strip()
    if kind not in KINDS:
        kind = "registers" if (ex.registers or ex.other) else "scope" if (ex.scope.get("period_ms") or "v_low" in ex.scope) else "serial" if ex.serial else "unknown"
        fixes.append(f"kind inferred as '{kind}'")
    ex.kind = kind
    c = _num(j.get("confidence"))
    ex.confidence = max(0.0, min(1.0, c / 100 if c and c > 1 else (c or 0.0)))
    ex.notes = str(j.get("notes") or "")[:500]
    bx = j.get("boxes") or {}
    if isinstance(bx, dict):
        for k, v in bx.items():
            try:
                b = [float(x) for x in v][:4]
                if len(b) == 4:
                    ex.boxes[chipdb.REG_ALIASES.get(str(k).upper().strip(), str(k).upper().strip())] = b
            except (TypeError, ValueError):
                pass
    return ex


def extract(model: ChatModel, images: list[bytes], intent: str = "", device: str = "", prompt: str | None = None) -> Extraction:
    prompt = prompt or EXTRACT_PROMPT.replace("a PIC24FJ256GB110 debugging tool", f"a {device or 'PIC'} debugging tool")
    msgs = [Message("user", prompt + (f"\nUser's intent (context only, do not let it bias readings): {intent}" if intent else ""), images)]
    last = None
    for attempt in range(2):
        txt = model.complete(msgs)
        fixes: list[str] = []
        try:
            ex = normalize(_loose_json(txt, fixes), fixes)
            ex.raw = txt[:2000]
            if attempt:
                ex.repaired.insert(0, "model re-asked for valid JSON")
            return ex
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            msgs = msgs + [Message("assistant", txt or ""), Message("user", REPAIR_PROMPT)]
    raise ModelError(f"vision extraction returned invalid JSON twice: {last}")


# ------------------------------------------------------------------ step 1b: multi-pass robustness (misread digits)
TRANSCRIBE_PROMPT = """You are an OCR transcriber for an embedded-debugging tool. The attached image (enlarged and sharpened)
shows register names with hex values. Transcribe EXACTLY, character by character. Do not interpret, do not correct,
do not infer values from what the firmware "should" do. Read every hex digit on its own; take special care with
look-alike glyphs: 8 / B / E / 3, 0 / D, 1 / 7, 5 / S, 6 / G. Omit a value you cannot read.
Return ONLY one JSON object:
{"kind": "registers"|"scope"|"serial"|"unknown", "registers": {"NAME": "0x...."}, "scope": {}, "serial": {},
 "boxes": {"NAME": [x0, y0, x1, y1]} (optional, fractions 0..1 of the image, the box around NAME and its value),
 "confidence": number (0..1), "notes": string}"""

TIEBREAK_PROMPT = """Transcribe EXACTLY, character by character, ONLY these register values from the image: {names}.
Look at each hex digit individually and zoom on the glyph shape (8 has two closed loops, B has a straight left side,
E has open right side, 3 has open left side; 0 vs D; 1 vs 7; 5 vs S; 6 vs G). Do not guess from context.
Return ONLY JSON: {{"kind": "registers", "registers": {{"NAME": "0x...."}}, "confidence": number, "notes": string}}"""

CONFUSABLE = [set("8BE3"), set("0D"), set("17"), set("5S"), set("6G")]


def confusable(a: str, b: str) -> bool:
    return a != b and any(a.upper() in g and b.upper() in g for g in CONFUSABLE)


def digit_diffs(a: int, b: int) -> list[tuple[int, str, str]]:
    """(position from left, digit in a, digit in b) for 16-bit hex values."""
    x, y = f"{a:04X}", f"{b:04X}"
    return [(i, x[i], y[i]) for i in range(len(x)) if x[i] != y[i]]


def ambiguity_note(name: str, vals: list[int]) -> str:
    vs = [f"0x{v:04X}" for v in vals]
    conf = sorted({f"{p}/{q}" for a in vals for b in vals if a < b for _, p, q in digit_diffs(a, b) if confusable(p, q)})
    return (f"{name} read as {' or '.join(vs)}" + (f" (commonly confused glyphs {', '.join(conf)})" if conf else ""))


def enhance_image(img: bytes, scale: int = 2, box: list[float] | None = None) -> bytes:
    """Upscale (LANCZOS), autocontrast + sharpen, optionally crop to a fractional box first. Returns PNG; original on failure."""
    try:
        import io
        from PIL import Image, ImageFilter, ImageOps
        im = Image.open(io.BytesIO(img)).convert("RGB")
        if box:
            W, H = im.size
            x0, y0, x1, y1 = box
            pad_x, pad_y = 0.04, 0.03
            c = (max(0, int((x0 - pad_x) * W)), max(0, int((y0 - pad_y) * H)), min(W, int((x1 + pad_x) * W)), min(H, int((y1 + pad_y) * H)))
            if c[2] - c[0] > 8 and c[3] - c[1] > 8:
                im = im.crop(c)
        w, h = im.size
        f = max(1, min(scale, int(4096 / max(w, h)) or 1))       # keep payload (= cost) bounded
        im = im.resize((w * f, h * f), Image.LANCZOS)
        im = ImageOps.autocontrast(im).filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=2))
        out = io.BytesIO(); im.save(out, "PNG")
        return out.getvalue()
    except Exception:
        return img


def _union_box(boxes: list[list[float]]) -> list[float] | None:
    boxes = [b for b in boxes if b and all(0 <= v <= 1 for v in b) and b[2] > b[0] and b[3] > b[1]]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def robust_extract(model: ChatModel, images: list[bytes], intent: str = "", device: str = "", passes: int = 2,
                   progress=None) -> Extraction:
    """Pass 1: normal read. Pass 2 (passes>=2, register images only): independent character-by-character transcription
    of an upscaled/sharpened copy. On disagreement, pass 3 (passes>=3): tie-break on the disputed names only (cropped
    when a box is known), then majority vote. Unresolved values land in ex.disputed (low confidence)."""
    passes = max(1, min(3, int(passes or 1)))
    say = progress or (lambda *_: None)
    ex = extract(model, images, intent, device)
    ex.passes.append({"pass": 1, "what": "read image -> JSON", "registers": len(ex.all_regs())})
    say(ex.passes[-1])
    if passes < 2 or not images or not ex.all_regs():
        return ex
    try:
        ex2 = extract(model, [enhance_image(i, 2) for i in images], "", device, prompt=TRANSCRIBE_PROMPT)
    except ModelError as e:
        ex.passes.append({"pass": 2, "what": "transcription pass failed", "error": str(e)[:200]})
        return ex
    r1, r2 = ex.all_regs(), ex2.all_regs()
    for k, v in r2.items():                    # values only the 2nd pass saw are added (not disputed)
        if k not in r1:
            (ex.registers if k in ex2.registers else ex.other)[k] = v
    for k, b in ex2.boxes.items():
        ex.boxes.setdefault(k, b)
    reads: dict[str, list[int]] = {k: [v] + ([r2[k]] if k in r2 else []) for k, v in r1.items()}
    diff = sorted(k for k, rs in reads.items() if len(set(rs)) > 1)
    ex.passes.append({"pass": 2, "what": "character-by-character transcription of 2x sharpened image",
                      "agree": sorted(k for k in r1 if k in r2 and r1[k] == r2[k]),
                      "disagree": {k: [f"0x{x:04X}" for x in reads[k]] for k in diff}})
    say(ex.passes[-1])
    if diff and passes >= 3:
        box = _union_box([ex.boxes.get(k) for k in diff])
        imgs3 = [enhance_image(images[0], 3, box)] if box else [enhance_image(i, 2) for i in images]
        try:
            ex3 = extract(model, imgs3, "", device, prompt=TIEBREAK_PROMPT.format(names=", ".join(diff)))
            r3 = ex3.all_regs()
            for k in diff:
                if k in r3:
                    reads[k].append(r3[k])
            ex.passes.append({"pass": 3, "what": f"tie-break on {', '.join(diff)}" + (" (cropped)" if box else " (full image)"),
                              "reads": {k: [f"0x{x:04X}" for x in reads[k]] for k in diff}})
        except ModelError as e:
            ex.passes.append({"pass": 3, "what": "tie-break failed", "error": str(e)[:200]})
        say(ex.passes[-1])
    for k in diff:
        rs = reads[k]
        cand = list(dict.fromkeys(rs))
        ex.candidates[k] = cand
        best = max(cand, key=rs.count)
        if rs.count(best) >= 2 and rs.count(best) > len(rs) / 2:
            ex.set_reg(k, best)               # majority vote
            ex.passes[-1].setdefault("voted", {})[k] = f"0x{best:04X} ({rs.count(best)}/{len(rs)})"
        else:
            ex.disputed[k] = cand              # keep pass-1 value, but low confidence
    if ex.disputed:
        ex.confidence = min(ex.confidence or 1.0, 0.4)
    return ex


def _port_consistency(ex: Extraction, writes: dict[str, int], device, port_mask_fn) -> list[str]:
    """Resolve ambiguous LATx/PORTx readings with chip facts. Returns notes."""
    notes = []
    regs = ex.all_regs()
    for name in list(ex.candidates):
        m = re.fullmatch(r"(LAT|PORT)([A-K])", name)
        if not m:
            continue
        port = m.group(2)
        mask = port_mask_fn(device, port)
        cands = ex.candidates[name]
        w = writes.get(f"LAT{port}", writes.get(f"PORT{port}"))
        pick, why = None, ""
        if w is not None:
            fits = [c for c in cands if c == w or (mask is not None and c == (w & mask))]
            if len(fits) == 1:
                pick, why = fits[0], f"matches the written 0x{w:04X}" + (f" & implemented mask 0x{mask:04X}" if mask is not None and fits[0] != w else "")
        if pick is None and mask is not None:     # implemented-bit check: a reading with bits set on unimplemented pins is impossible
            fits = [c for c in cands if c & ~mask & 0xFFFF == 0]
            if len(fits) == 1 and len(cands) > 1:
                pick, why = fits[0], f"the other reading sets unimplemented bits (mask 0x{mask:04X})"
        if pick is None:                          # LATx vs PORTx should agree on implemented output pins
            other = regs.get(("PORT" if m.group(1) == "LAT" else "LAT") + port)
            if other is not None and not ex.candidates.get(("PORT" if m.group(1) == "LAT" else "LAT") + port):
                tris = regs.get(f"TRIS{port}", 0)
                cmp = (mask if mask is not None else 0xFFFF) & ~tris & 0xFFFF
                fits = [c for c in cands if (c ^ other) & cmp == 0]
                if len(fits) == 1:
                    pick, why = fits[0], f"agrees with the clear reading of {('PORT' if m.group(1) == 'LAT' else 'LAT') + port} on output pins"
        if pick is not None:
            ex.set_reg(name, pick)
            ex.disputed.pop(name, None)
            notes.append(f"{ambiguity_note(name, cands)}; chose 0x{pick:04X} because it {why}")
    return notes


# ------------------------------------------------------------------ intent + user code
@dataclass
class Intent:
    text: str = ""
    period_s: float | None = None      # desired timer/interrupt period
    wave_period_s: float | None = None  # expected period of the pin waveform on a scope
    baud: int | None = None
    timer: int = 1
    toggle: bool = False
    fcy_hz: float | None = None         # clock stated by the user (Fcy, Fosc/2, or crystal)
    clock_note: str = ""
    vdd: float | None = None
    topics: set = field(default_factory=set)   # timer / uart / clock / port
    rx_pin: int | None = None           # "RX on RP4"
    tx_pin: int | None = None
    receive: bool = False
    writes: dict = field(default_factory=dict)  # "LATA = 0xBBAA" in the intent


def parse_intent(text: str) -> Intent:
    it = Intent(text or "")
    t = (text or "").lower()
    if not t.strip():
        return it
    mb = re.search(r"(\d{3,7})\s*(?:baud|bps|bd)\b", t) or re.search(r"(?:uart|baud)\D{0,12}(\d{3,7})", t)
    if mb:
        it.baud = int(mb.group(1))
    # ---- clock stated in the intent
    def hz(num, unit):
        return float(num) * {"m": 1e6, "k": 1e3, "": 1.0}[unit]
    m = re.search(r"\bfcy\s*(?:=|of|is|at|:)?\s*(\d+(?:\.\d+)?)\s*(m|k)?hz", t)
    if m:
        it.fcy_hz, it.clock_note = hz(m.group(1), m.group(2) or ""), f"Fcy {m.group(1)} {(m.group(2) or '').upper()}Hz stated in the intent"
    else:
        m = re.search(r"\b(?:fosc|osc(?:illator)?|clock|sysclk)\s*(?:=|of|is|at|:)?\s*(\d+(?:\.\d+)?)\s*(m|k)?hz", t)
        m2 = re.search(r"(\d+(?:\.\d+)?)\s*(m|k)?hz\s*(?:crystal|xtal|quartz)|(?:crystal|xtal)\s*(?:=|of|:)?\s*(\d+(?:\.\d+)?)\s*(m|k)?hz", t)
        if m:
            it.fcy_hz, it.clock_note = hz(m.group(1), m.group(2) or "") / 2, f"Fosc {m.group(1)} {(m.group(2) or '').upper()}Hz stated in the intent -> Fcy = Fosc/2"
        elif m2:
            n, u = (m2.group(1), m2.group(2)) if m2.group(1) else (m2.group(3), m2.group(4))
            it.fcy_hz, it.clock_note = hz(n, u or "") / 2, f"{n} {(u or '').upper()}Hz crystal stated in the intent, assumed no PLL -> Fcy = Fosc/2"
    mv = re.search(r"(\d(?:\.\d+)?)\s*v\b(?:\s*(?:logic|vdd|supply|cmos|ttl))?", t)
    if mv and 1.0 <= float(mv.group(1)) <= 5.5:
        it.vdd = float(mv.group(1))
    mr = re.search(r"\brx\w*\s*(?:pin\s*)?(?:on|=|at|:)?\s*rp(\d+)", t)
    if mr:
        it.rx_pin = int(mr.group(1))
    mt = re.search(r"\btx\w*\s*(?:pin\s*)?(?:on|=|at|:)?\s*rp(\d+)", t)
    if mt:
        it.tx_pin = int(mt.group(1))
    it.receive = bool(re.search(r"\brx\b|receiv|read\s*back|loopback|echo|u\drxreg", t))
    for m in re.finditer(r"\b((?:lat|port|tris)[a-k])\s*=\s*(0x[0-9a-f]+|\d+)", t):
        it.writes[m.group(1).upper()] = int(m.group(2), 0)
    if mb or re.search(r"\buart|\busart|baud|\bu\d(?:tx|rx|brg|mode|sta)|serial", t):
        it.topics.add("uart")
    if re.search(r"\btimer|\btmr|\bt\dcon|\bpr\d\b|interrupt every|toggl|blink|square|pwm|period|\d\s*(?:ms|us|µs)\b", t):
        it.topics.add("timer")
    if re.search(r"\bclock|\bosc|\bpll|\bfrc|\bfcy|\bfosc|crystal|mhz", t):
        it.topics.add("clock")
    if it.writes or re.search(r"\b(?:lat|port|tris)[a-k]\b|\br[a-k]\d+\b|\bpin\b|\bled\b|gpio", t):
        it.topics.add("port")
    tt = re.sub(r"(\d{3,7})\s*(?:baud|bps|bd)\b", " ", t)
    tt = re.sub(r"(\d+(?:\.\d+)?)\s*[mk]?hz", " ", tt) if not re.search(r"square|pwm|wave|signal", t) else tt
    if re.search(r"\d\s*(?:us|µs|ms|s|sec|hz|khz)\b", tt):
        try:
            sp = parse_request(tt)
            it.period_s, it.timer = sp.period_s, sp.timer
        except Exception:
            pass
    it.toggle = bool(re.search(r"toggl|blink", t)) and not re.search(r"square|waveform|pwm|signal period|scope period", t)
    if it.period_s:
        # "toggle every 10 ms" -> pin period 20 ms; "10 ms square wave / 100 Hz" -> pin period 10 ms
        it.wave_period_s = it.period_s * 2 if it.toggle else it.period_s
    return it


_ASSIGN = re.compile(r"\b([A-Z][A-Z0-9]{2,7})\s*=\s*(0[xX][0-9A-Fa-f]+|0[bB][01]+|\d+)\s*[;uUlL]")
_BITS = re.compile(r"\b([A-Z][A-Z0-9]{2,7})bits\.([A-Z0-9]+)\s*=\s*(0[xX][0-9A-Fa-f]+|0[bB][01]+|\d+)\s*;")
_PRAGMA = re.compile(r"#\s*pragma\s+config\s+(\w+)\s*=\s*(\w+)")
_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)\s+(.+?)\s*$", re.M)
_EXPR_ASSIGN = re.compile(r"\b([A-Z][A-Z0-9]{2,7})\s*=\s*([^;=]+);")


def _c_eval(expr: str, defines: dict[str, str], depth: int = 0) -> int | None:
    """Evaluate a constant C integer expression (macros, UL suffixes, + - * / % << >> | & ()) safely."""
    if depth > 8:
        return None
    e = re.sub(r"\b(0[xX][0-9A-Fa-f]+|\d+)[uUlL]+\b", r"\1", expr)
    e = re.sub(r"\(\s*(?:unsigned\s+)?(?:long|int|uint16_t|uint32_t)\s*\)", "", e)
    def sub(m):
        n = m.group(0)
        if n in defines:
            v = _c_eval(defines[n], defines, depth + 1)
            return str(v) if v is not None else n
        return n
    e = re.sub(r"\b[A-Za-z_]\w*\b", sub, e)
    if not re.fullmatch(r"[0-9xXa-fA-F\s+\-*/%()<>|&]+", e) or re.search(r"[g-wyzG-WYZ]", e):
        return None
    e = re.sub(r"(?<![/])/(?![/])", "//", e)
    try:
        v = eval(compile(e, "<c>", "eval"), {"__builtins__": {}}, {})
    except Exception:
        return None
    return int(v) if isinstance(v, int) else None


def code_registers(code: str) -> tuple[dict[str, int], dict[str, str]]:
    """Literal register writes in the user's C code (whole-register then bitfield writes) + config pragmas."""
    regs: dict[str, int] = {}
    clean = re.sub(r"/\*.*?\*/", "", code or "", flags=re.S)
    clean = re.sub(r"//[^\n]*", "", clean)
    defines = {m.group(1): m.group(2) for m in _DEFINE.finditer(clean)}
    for m in _EXPR_ASSIGN.finditer(clean):
        if m.group(1) in chipdb.CORE_NAMES:
            v = _c_eval(m.group(2), defines)
            if v is not None:
                regs[m.group(1)] = v & 0xFFFF
    for m in _BITS.finditer(clean):
        r = chipdb.REGISTERS.get(m.group(1))
        f = r.field(m.group(2)) if r else None
        if f:
            v = regs.get(r.name, r.reset)
            regs[r.name] = (v & ~f.mask) | ((int(m.group(3), 0) << f.lsb) & f.mask)
    pr = {m.group(1).upper(): m.group(2).upper() for m in _PRAGMA.finditer(clean)}
    return regs, pr


# ------------------------------------------------------------------ step 2: deterministic analysis
@dataclass
class Row:
    register: str
    value: str
    decoded: str
    expected: str
    status: str          # ok | bad | warn | info
    page: int | None = None
    note: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class Fix:
    target: str           # e.g. "T1CONbits.TCKPS"
    old: str | None
    new: str
    c_line: str           # line to put in code
    reason: str

    def to_dict(self):
        return asdict(self)


@dataclass
class Analysis:
    rows: list[Row] = field(default_factory=list)
    causes: list[str] = field(default_factory=list)
    fixes: list[Fix] = field(default_factory=list)
    clock: dict = field(default_factory=dict)
    computed: dict = field(default_factory=dict)
    pages: list[int] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)   # anomalies that need more evidence (-> UNCERTAIN, never OK)
    explained: list[str] = field(default_factory=list)   # anomalies proven harmless (e.g. unimplemented port bits)

    def bad(self) -> bool:
        return any(r.status == "bad" for r in self.rows)


def implied_clock(regs: dict[str, int], fallback_fosc: float, crystal_hz: float = 8e6) -> dict:
    """Fosc implied by OSCCON.COSC + CLKDIV (falls back to the board profile when OSCCON is unknown)."""
    if "OSCCON" not in regs:
        return {"fosc_hz": fallback_fosc, "source": "board profile (OSCCON not in image)"}
    cosc = (regs["OSCCON"] >> 12) & 7
    cl = regs.get("CLKDIV", chipdb.REGISTERS["CLKDIV"].reset)
    rcdiv, cpdiv = (cl >> 8) & 7, (cl >> 6) & 3
    if cosc == 0:
        f = 8e6
    elif cosc in (1, 3):
        f = 32e6 / (1 << cpdiv)
    elif cosc == 2:
        f = crystal_hz
    elif cosc == 4:
        f = 32768.0
    elif cosc == 5:
        f = 31e3
    elif cosc == 6:
        f = 500e3
    else:
        f = RCDIV_HZ[rcdiv]
    return {"fosc_hz": f, "cosc": cosc, "source": f"OSCCON.COSC={cosc} ({COSC_NAMES[cosc]})"
            + (f", CLKDIV.CPDIV={cpdiv}" if cosc in (1, 3) else f", CLKDIV.RCDIV={rcdiv}" if cosc == 7 else "")}


def _fmt_t(s: float) -> str:
    return f"{s*1e3:.4g} ms" if s >= 1e-3 else f"{s*1e6:.4g} us"


def port_mask(device, port: str) -> int | None:
    """Implemented-bit mask of PORTx/LATx from the device's EDC SFR fields (None if unknown)."""
    name = getattr(device, "name", device) or ""
    if hasattr(device, "sfr") and getattr(device, "source", "") != "built-in":
        for rn in (f"PORT{port}", f"LAT{port}"):
            r = device.sfr(rn)
            if r is not None and r.fields:
                return sum(((1 << f.width) - 1) << f.lsb for f in r.fields)
    return BUILTIN_PORT_MASKS.get(str(name).upper(), {}).get(port)


# from the PIC24FJ256GB110 EDC (Microchip pack): PORTA has RA0-7, RA9, RA10, RA14, RA15 on the 100-pin part
BUILTIN_PORT_MASKS = {"PIC24FJ256GB110": {"A": 0xC6FF}}
PPS_OUT = {0: "NULL (pin is a normal I/O)", 1: "C1OUT", 2: "C2OUT", 3: "U1TX", 4: "U1RTS", 5: "U2TX", 6: "U2RTS",
           7: "SDO1", 8: "SCK1OUT", 9: "SS1OUT", 18: "OC1", 19: "OC2"}
_LAT_WRITE = re.compile(r"\b((?:LAT|PORT)[A-K])\s*=\s*(0[xX][0-9A-Fa-f]+|\d+)\s*[uUlL]*\s*;")


def _pps_map(regs: dict[str, int]) -> dict[int, int]:
    """RPn -> output function code from RPORx values."""
    out = {}
    for k, v in regs.items():
        m = re.fullmatch(r"RPOR(\d+)", k)
        if m:
            n = int(m.group(1))
            out[2 * n] = v & 0x3F
            out[2 * n + 1] = (v >> 8) & 0x3F
    return out


def analyze(ex: Extraction, intent: Intent, code: str = "", fosc_hz: float = 32e6, expected_cosc: int = 1,
            crystal_hz: float = 8e6, clock_explicit: bool = False, device=None) -> Analysis:
    A = Analysis()
    regs = dict(ex.registers)
    other = dict(ex.other)
    code_regs, code_pr = code_registers(code)
    profile_fosc = fosc_hz
    if intent.fcy_hz:
        fosc_hz, clock_known, clock_src = intent.fcy_hz * 2, True, intent.clock_note
    elif clock_explicit:
        clock_known, clock_src = True, f"Fosc {fosc_hz/1e6:g} MHz given explicitly"
    else:
        clock_known, clock_src = False, f"ASSUMED board profile Fosc {fosc_hz/1e6:g} MHz (not stated in the intent or image)"
    clk = implied_clock(regs, fosc_hz, crystal_hz)
    if "OSCCON" in regs:
        clock_known = True
    else:
        clk["source"] = clock_src
    A.clock = {**clk, "expected_fosc_hz": fosc_hz, "fcy_hz": clk["fosc_hz"] / 2, "clock_known": clock_known,
               "assumption": clock_src}
    fcy = clk["fosc_hz"] / 2
    pg = A.pages
    topics = intent.topics if intent.text else set()

    def relevant(t: str) -> bool:
        if not topics:
            return True
        if t == "clock":
            return bool(topics & {"clock", "timer", "uart"})
        return t in topics

    def row(*a, **k):
        r = Row(*a, **k); A.rows.append(r)
        if r.page and r.page not in pg:
            pg.append(r.page)
        return r

    def mark(topic: str, n_rows: int, n_causes: int, n_fixes: int):
        """Relevance filter: findings outside the intent become INFO, their causes/fixes are dropped."""
        if relevant(topic):
            return
        for r in A.rows[n_rows:]:
            if r.status != "info":
                r.note = (r.note + "; " if r.note else "") + f"not related to the intent ({r.status} demoted to info)"
                r.status = "info"
        del A.causes[n_causes:]; del A.fixes[n_fixes:]

    def snap():
        return len(A.rows), len(A.causes), len(A.fixes)

    s0 = snap()

    # ---- clock
    if "OSCCON" in regs:
        v = regs["OSCCON"]; cosc, nosc = (v >> 12) & 7, (v >> 8) & 7
        ok = cosc == expected_cosc or bool(intent.fcy_hz)
        row("OSCCON", f"0x{v:04X}", f"COSC={cosc:03b} {COSC_NAMES[cosc]}; NOSC={nosc:03b}; LOCK={(v >> 5) & 1}",
            f"COSC={expected_cosc:03b} {COSC_NAMES[expected_cosc]}", "ok" if ok else "bad", 123)
        if not ok and not intent.fcy_hz:
            A.causes.append(f"CPU is running from {COSC_NAMES[cosc]} instead of {COSC_NAMES[expected_cosc]}: Fosc = "
                            f"{clk['fosc_hz']/1e6:g} MHz, expected {fosc_hz/1e6:g} MHz (x{fosc_hz/clk['fosc_hz']:.3g} slower). "
                            "Usually the FNOSC config pragma is wrong (FRC/FRCDIV instead of FRCPLL) or the PLL never locked.")
            A.fixes.append(Fix("#pragma config FNOSC", code_pr.get("FNOSC"), "FRCPLL", "#pragma config FNOSC = FRCPLL",
                               "select FRC + 96 MHz PLL so Fosc = 32 MHz (DS39897C p.123)"))
            if expected_cosc == 1:
                A.fixes.append(Fix("#pragma config PLL_96MHZ", code_pr.get("PLL_96MHZ"), "ON", "#pragma config PLL_96MHZ = ON",
                                   "PLL must be enabled at reset"))
        if cosc != nosc:
            row("OSCCON.NOSC", f"{nosc:03b}", "clock switch pending (NOSC != COSC)", f"{cosc:03b}", "warn", 123)
    if "CLKDIV" in regs:
        v = regs["CLKDIV"]; d = chipdb.REGISTERS["CLKDIV"].decode(v)
        cos = clk.get("cosc")
        status, exp = "info", "CPDIV=00 (32 MHz)"
        if cos in (1, 3):
            status = "ok" if d["CPDIV"] == 0 else "bad"
            if d["CPDIV"]:
                A.causes.append(f"CLKDIV.CPDIV={d['CPDIV']:02b} divides the 32 MHz PLL branch down to {32 >> d['CPDIV']} MHz.")
                A.fixes.append(Fix("CLKDIVbits.CPDIV", str(d["CPDIV"]), "0", "CLKDIVbits.CPDIV = 0;", "full 32 MHz system clock (p.125)"))
        row("CLKDIV", f"0x{v:04X}", f"RCDIV={d['RCDIV']:03b} ({RCDIV_HZ[d['RCDIV']]/1e6:g} MHz) CPDIV={d['CPDIV']:02b} DOZEN={d['DOZEN']}",
            exp, status, 125)
    if clk.get("cosc") is not None:
        row("Fcy (implied)", f"{fcy/1e6:g} MHz", clk["source"], f"{fosc_hz/2/1e6:g} MHz",
            "ok" if abs(fcy - fosc_hz / 2) < 1 else "bad", 123)

    mark("clock", *s0)
    s0 = snap()
    # ---- timers
    want = solve(TimerSpec(timer=intent.timer, period_s=intent.period_s, fosc_hz=fosc_hz)) if intent.period_s else None
    for n, (con, prn) in {1: ("T1CON", "PR1"), 2: ("T2CON", "PR2"), 3: ("T3CON", "PR3")}.items():
        if con not in regs and prn not in regs:
            continue
        mine = want is not None and n == intent.timer
        if con in regs:
            v = regs[con]; d = chipdb.REGISTERS[con].decode(v); ps = PRESCALERS[d["TCKPS"]]
            exp = f"TON=1 TCS=0 TCKPS={want.tckps:02b} (1:{want.prescale})" if mine else "TON=1 TCS=0"
            good = d["TON"] == 1 and d["TCS"] == 0 and (not mine or d["TCKPS"] == want.tckps)
            row(con, f"0x{v:04X}", f"TON={d['TON']} TCS={d['TCS']} TGATE={d['TGATE']} TCKPS={d['TCKPS']:02b} (1:{ps})",
                exp, "ok" if good else "bad", chipdb.REGISTERS[con].page)
            if not d["TON"]:
                A.causes.append(f"{con}.TON=0: Timer{n} is stopped, it never counts or interrupts.")
                A.fixes.append(Fix(f"{con}bits.TON", "0", "1", f"{con}bits.TON = 1;", "start the timer"))
            if d["TCS"]:
                A.causes.append(f"{con}.TCS=1: Timer{n} counts the external T{n}CK pin instead of Fcy.")
                A.fixes.append(Fix(f"{con}bits.TCS", "1", "0", f"{con}bits.TCS = 0;", "clock from Fosc/2"))
            if mine and d["TCKPS"] != want.tckps:
                A.causes.append(f"{con}.TCKPS={d['TCKPS']:02b} gives prescale 1:{ps} but 1:{want.prescale} is needed "
                                f"(timer runs {want.prescale/ps:.3g}x {'fast' if ps < want.prescale else 'slow'}).")
                A.fixes.append(Fix(f"{con}bits.TCKPS", str(d["TCKPS"]), str(want.tckps), f"{con}bits.TCKPS = {want.tckps};",
                                   f"prescale 1:{want.prescale} (DS39897C p.{chipdb.REGISTERS[con].page})"))
        if prn in regs:
            pr = regs[prn]
            ok = (not mine) or pr == want.period_reg
            row(prn, f"0x{pr:04X}", f"{pr} decimal" + (" (reset value - never loaded)" if pr == 0xFFFF else ""),
                f"{want.period_reg} (0x{want.period_reg:04X})" if mine else "-", "ok" if ok else "bad", chipdb.REGISTERS[con].page)
            if mine and not ok:
                A.fixes.append(Fix(prn, str(pr), str(want.period_reg), f"{prn} = {want.period_reg};",
                                   f"(PR+1) x 1:{want.prescale} x Tcy = {_fmt_t(want.achieved_s)}"))
        if con in regs and prn in regs:
            ps = PRESCALERS[(regs[con] >> 4) & 3]
            per = timer_period_s(fcy, ps, regs[prn])
            A.computed[f"timer{n}_period_s"] = per
            ok = (not mine) or abs(per - intent.period_s) / intent.period_s <= 0.01
            row(f"Timer{n} period", _fmt_t(per), f"(PR+1) x 1:{ps} / Fcy {fcy/1e6:g} MHz",
                _fmt_t(intent.period_s) if mine else "-", "ok" if ok else "bad", chipdb.REGISTERS[con].page)
            if mine and not ok:
                A.causes.append(f"Timer{n} period is {_fmt_t(per)} instead of {_fmt_t(intent.period_s)} "
                                f"(x{per/intent.period_s:.3g}).")

    mark("timer", *s0)
    s0 = snap()
    # ---- UART
    if any(r in regs for r in ("U1MODE", "U1STA", "U1BRG", "RPINR18")) or any(k.startswith("RPOR") for k in regs):
        mode = regs.get("U1MODE")
        brgh = (mode >> 3) & 1 if mode is not None else 0
        if mode is not None:
            d = chipdb.REGISTERS["U1MODE"].decode(mode)
            row("U1MODE", f"0x{mode:04X}", f"UARTEN={d['UARTEN']} BRGH={d['BRGH']} PDSEL={PDSEL[d['PDSEL']]} STSEL={d['STSEL']}"
                f"{' LPBACK=1' if d['LPBACK'] else ''}", "UARTEN=1" + (f" BRGH={solve_baud(fosc_hz/2, intent.baud).brgh}" if intent.baud else ""),
                "ok" if d["UARTEN"] and not d["LPBACK"] else "bad", 202)
            if not d["UARTEN"]:
                A.causes.append("U1MODE.UARTEN=0: UART1 is disabled."); A.fixes.append(Fix("U1MODEbits.UARTEN", "0", "1", "U1MODEbits.UARTEN = 1;", "enable UART1"))
            if d["LPBACK"]:
                A.causes.append("U1MODE.LPBACK=1: loopback mode, nothing leaves the TX pin.")
        if "U1STA" in regs:
            d = chipdb.REGISTERS["U1STA"].decode(regs["U1STA"])
            no_rx = intent.receive and not d["URXDA"]
            st = "bad" if (not d["UTXEN"] or d["OERR"] or d["FERR"] or no_rx) else "ok"
            row("U1STA", f"0x{regs['U1STA']:04X}",
                f"URXDA={d['URXDA']} ({'byte waiting' if d['URXDA'] else 'RX FIFO EMPTY'}) OERR={d['OERR']} FERR={d['FERR']} PERR={d['PERR']} "
                f"RIDLE={d['RIDLE']} | UTXEN={d['UTXEN']} UTXBF={d['UTXBF']} TRMT={d['TRMT']} ({'TX shift reg empty' if d['TRMT'] else 'transmitting'})",
                "UTXEN=1 OERR=0 FERR=0" + (" URXDA=1 before reading U1RXREG" if intent.receive else ""), st, 204)
            if no_rx:
                rxv = other.get("U1RXREG")
                reads = bool(re.search(r"U1RXREG", code or ""))
                polls = bool(re.search(r"URXDA|U1STA\s*&\s*(?:0x0*1|1)\b|_U1RXIF|U1RXIF", code or ""))
                A.causes.append("U1STA.URXDA=0: the receive FIFO is EMPTY - no byte has arrived"
                                + (f", so U1RXREG=0x{rxv:04X} is stale/zero, not received data" if rxv is not None else "")
                                + ". The code must wait for URXDA=1 (and, in a loopback test, for the TX byte to finish: TRMT=1) "
                                "before reading U1RXREG (DS39897C p.204).")
                if reads and not polls:
                    A.causes.append("Your code reads U1RXREG without polling U1STAbits.URXDA - it reads an empty FIFO.")
                A.fixes.append(Fix("U1RXREG read", None, "poll URXDA", "while (!U1STAbits.URXDA) { }   /* then: c = U1RXREG; */",
                                   "wait until a byte is received before reading U1RXREG (p.204)"))
            if not d["UTXEN"]:
                A.causes.append("U1STA.UTXEN=0: transmitter disabled (set it AFTER UARTEN=1).")
                A.fixes.append(Fix("U1STAbits.UTXEN", "0", "1", "U1STAbits.UTXEN = 1;", "enable TX after UARTEN (p.204)"))
            if d["OERR"]:
                A.causes.append("U1STA.OERR=1: receive overrun - the RX FIFO is full and reception stopped until OERR is cleared.")
            if d["FERR"]:
                A.causes.append("U1STA.FERR=1: framing errors - typical of a baud-rate mismatch.")
        if "U1BRG" in regs:
            brg = regs["U1BRG"]; baud = baud_from_brg(fcy, brg, brgh)
            A.computed["baud"] = baud
            cands = {}
            if intent.baud and not clock_known:
                for f in sorted({2e6, 4e6, 8e6, 16e6, 20e6, 30e6, 40e6, fcy}):
                    b = baud_from_brg(f, brg, brgh)
                    cands[f] = b
                A.computed["baud_candidates"] = {f"{f/1e6:g} MHz": round(b) for f, b in cands.items()}
            match = [f for f, b in cands.items() if abs(b - intent.baud) / intent.baud <= 0.02]
            if intent.baud and not clock_known and match and abs(baud - intent.baud) / intent.baud > 0.02:
                row("U1BRG", f"0x{brg:04X}", f"{brg}, BRGH={brgh}: " + ", ".join(f"{b:,.0f} @ Fcy {f/1e6:g} MHz" for f, b in cands.items()),
                    f"{intent.baud:,} baud", "warn", 200, "clock not stated - baud depends on Fcy")
                A.uncertain.append(f"U1BRG={brg} (BRGH={brgh}) gives {intent.baud:,} baud only if Fcy = "
                                   + " or ".join(f"{f/1e6:g} MHz" for f in match)
                                   + f"; with the assumed profile Fcy {fcy/1e6:g} MHz it would be {baud:,.0f} baud. "
                                   "State the clock in the intent (e.g. 'Fcy 4 MHz') to decide.")
            elif intent.baud:
                err = (baud - intent.baud) / intent.baud * 100
                sol = solve_baud(fosc_hz / 2, intent.baud)
                row("U1BRG", f"0x{brg:04X}", f"{brg} -> {baud:,.0f} baud @ Fcy {fcy/1e6:g} MHz [{A.clock['source']}], BRGH={brgh} ({err:+.2f}%)",
                    f"{sol.brg} with BRGH={sol.brgh} -> {sol.actual:,.0f} baud ({sol.error_pct:+.2f}%)",
                    "ok" if abs(err) <= 2 else "bad", 200)
                if abs(err) > 2:
                    alt = baud_from_brg(fcy, brg, 1 - brgh)
                    if abs(alt - intent.baud) / intent.baud <= 0.02:
                        A.causes.append(f"BRGH mismatch: U1BRG={brg} was computed for BRGH={1-brgh} "
                                        f"({4 if brgh == 0 else 16}x clock) but U1MODE.BRGH={brgh}, so the line runs at {baud:,.0f} baud.")
                    else:
                        A.causes.append(f"Baud rate is {baud:,.0f} instead of {intent.baud:,} ({err:+.1f}%).")
                    if sol.brgh != brgh:
                        A.fixes.append(Fix("U1MODEbits.BRGH", str(brgh), str(sol.brgh), f"U1MODEbits.BRGH = {sol.brgh};", "match the U1BRG formula (p.200)"))
                    if sol.brg != brg:
                        A.fixes.append(Fix("U1BRG", str(brg), str(sol.brg), f"U1BRG = {sol.brg};",
                                           f"Fcy/({4 if sol.brgh else 16} x baud) - 1 at Fcy {fosc_hz/2/1e6:g} MHz"))
            else:
                row("U1BRG", f"0x{brg:04X}", f"{brg} -> {baud:,.0f} baud (BRGH={brgh})", "-", "info", 200)
        # ---- PPS routing
        pps = _pps_map(regs)
        for k in sorted((k for k in regs if k.startswith("RPOR")), key=lambda x: int(x[4:])):
            n = int(k[4:]); lo, hi = regs[k] & 0x3F, (regs[k] >> 8) & 0x3F
            bad_dir = intent.rx_pin in (2 * n, 2 * n + 1) and pps.get(intent.rx_pin) not in (None, 0)
            row(k, f"0x{regs[k]:04X}", f"RP{2*n}R={lo} ({PPS_OUT.get(lo, f'fn {lo}')}), RP{2*n+1}R={hi} ({PPS_OUT.get(hi, f'fn {hi}')})",
                (f"RP{intent.rx_pin}R=0 (RX pin must not be an output)" if bad_dir else "-"), "bad" if bad_dir else "info",
                chipdb.REGISTERS[k].page)
            if bad_dir:
                A.causes.append(f"PPS direction mismatch: RPOR{n}.RP{intent.rx_pin}R={pps[intent.rx_pin]} drives RP{intent.rx_pin} as an "
                                f"OUTPUT ({PPS_OUT.get(pps[intent.rx_pin], '?')}) although it is the UART RX pin (p.138/{chipdb.REGISTERS[k].page}).")
                A.fixes.append(Fix(f"RPOR{n}bits.RP{intent.rx_pin}R", str(pps[intent.rx_pin]), "0", f"RPOR{n}bits.RP{intent.rx_pin}R = 0;",
                                   "RX pin is an input - map it with RPINR18 only"))
        if "RPINR18" in regs:
            rx = regs["RPINR18"] & 0x3F
            want_rx = intent.rx_pin
            bad_rx = want_rx is not None and rx != want_rx
            row("RPINR18", f"0x{regs['RPINR18']:04X}", f"U1RXR={rx} (U1RX <- RP{rx})" + (" (unmapped)" if rx == 0x3F else ""),
                f"U1RXR={want_rx}" if want_rx is not None else "-", "bad" if bad_rx else "info", 147)
            if bad_rx:
                A.causes.append(f"RPINR18.U1RXR={rx}: UART1 RX listens on RP{rx}, not RP{want_rx}.")
                A.fixes.append(Fix("RPINR18bits.U1RXR", str(rx), str(want_rx), f"RPINR18bits.U1RXR = {want_rx};", "route U1RX (p.147)"))
            tx_pins = [p for p, f in pps.items() if f == 3]
            if tx_pins and rx not in tx_pins and intent.receive and rx != 0x3F:
                A.uncertain.append(f"Loopback check: U1TX is on RP{tx_pins[0]} but U1RX listens on RP{rx} - those two pins must be "
                                   "wired together for a loopback test.")
    mark("uart", *s0)
    s0 = snap()

    # ---- scope logic levels (always relevant: they are physical evidence)
    vh, vl = ex.scope.get("v_high"), ex.scope.get("v_low")
    if vh is not None or vl is not None:
        vdd = ex.scope.get("vdd") or intent.vdd or 3.3
        lo_bad = vl is not None and vl > 0.3 * vdd
        hi_bad = vh is not None and vh < 0.7 * vdd
        row("Logic levels", f"H {vh if vh is not None else '?'} V / L {vl if vl is not None else '?'} V",
            f"VIL max {0.3*vdd:.2f} V, VIH min {0.7*vdd:.2f} V at VDD {vdd:g} V", f"L < {0.3*vdd:.2f} V, H > {0.7*vdd:.2f} V",
            "bad" if (lo_bad or hi_bad) else "ok", None)
        if lo_bad or hi_bad:
            what = (f"logic LOW sits at {vl:g} V (should be ~0 V, max {0.3*vdd:.2f} V)" if lo_bad else
                    f"logic HIGH only reaches {vh:g} V (needs > {0.7*vdd:.2f} V)")
            A.causes.append(f"Invalid logic level: {what}. A mid-rail level means two drivers fight on the same wire (bus "
                            "contention), or a missing pull-up/pull-down, or heavy loading / a series resistor divider.")
            if "uart" in intent.topics or intent.baud:
                pin = intent.rx_pin
                pr = f"RP{pin}" if pin is not None else "the RX pin"
                n = pin // 2 if pin is not None else None
                ccode = _pps_map(code_regs).get(pin) if pin is not None else None
                A.causes.append(f"For UART this is the classic PPS direction mismatch: {pr} configured as an OUTPUT "
                                f"(RPOR{n if n is not None else 'x'}.RP{pin if pin is not None else 'n'}R = 3 = U1TX) instead of an input "
                                f"mapped via RPINR18.U1RXR - the PIC's TX driver fights the other device's TX (DS39897C p.138, p.147)."
                                + (f" Your code does exactly this: RP{pin}R = {ccode}." if ccode else
                                   (" Your code does not write that RPOR field - check the other device / wiring." if code.strip() and pin is not None else "")))
                if pin is not None:
                    A.fixes.append(Fix(f"RPOR{n}bits.RP{pin}R", str(ccode) if ccode is not None else None, "0",
                                       f"RPOR{n}bits.RP{pin}R = 0;", "RX pin must not drive U1TX"))
                    A.fixes.append(Fix("RPINR18bits.U1RXR", None, str(pin), f"RPINR18bits.U1RXR = {pin};", "U1RX input on the RX pin"))

    # ---- ports: written vs read, implemented-bit mask
    s0 = snap()
    port_written = dict(intent.writes)
    clean = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", code or "", flags=re.S))
    for m in _LAT_WRITE.finditer(clean):
        port_written.setdefault(m.group(1), int(m.group(2), 0) & 0xFFFF)
    for name in sorted(k for k in other if re.fullmatch(r"(LAT|PORT)[A-K]", k)):
        val, port = other[name], name[-1]
        w = port_written.get(f"LAT{port}", port_written.get(f"PORT{port}"))
        mask = port_mask(device, port)
        if w is None:
            row(name, f"0x{val:04X}", "read value", "-", "info", None)
            continue
        if val == w:
            row(name, f"0x{val:04X}", f"equals the written 0x{w:04X}", f"0x{w:04X}", "ok", None)
        elif mask is not None and (w & mask) == val:
            gone = w & ~mask
            row(name, f"0x{val:04X}", f"written 0x{w:04X} & implemented mask 0x{mask:04X} = 0x{w & mask:04X}",
                f"0x{w & mask:04X}", "ok", None, f"bits 0x{gone:04X} are unimplemented on this device and always read 0")
            A.explained.append(f"Not a bug: {name} reads 0x{val:04X} because PORT{port} bits "
                               + ", ".join(f"R{port}{b}" for b in range(16) if gone >> b & 1)
                               + f" are not implemented on {getattr(device, 'name', device) or 'this device'} (implemented mask 0x{mask:04X}, "
                               "from the Microchip device file). Unimplemented bits read as 0; the write worked.")
        elif mask is None and (val & ~w) == 0:
            row(name, f"0x{val:04X}", f"written 0x{w:04X}; missing bits 0x{w & ~val:04X}", f"0x{w:04X}", "warn", None,
                "device pin map unknown")
            A.uncertain.append(f"{name} reads 0x{val:04X} after writing 0x{w:04X}: the missing bits (0x{w & ~val:04X}) are most likely "
                               "unimplemented pins on this package (they read as 0). Pick the device (--device) to confirm from its EDC file.")
        else:
            row(name, f"0x{val:04X}", f"written 0x{w:04X}", f"0x{(w & mask) if mask is not None else w:04X}", "bad", None)
            A.causes.append(f"{name} reads 0x{val:04X} but 0x{w:04X} was written: implemented pins differ - check analog mode "
                            "(AD1PCFG/ANSx), TRIS, a peripheral owning the pin, or read-modify-write on PORTx instead of LATx.")
    mark("port", *s0)

    # ---- scope measurement
    if ex.scope.get("period_ms"):
        meas = ex.scope["period_ms"] / 1e3
        A.computed["measured_period_s"] = meas
        exp = intent.wave_period_s
        if exp is None and "timer1_period_s" in A.computed:
            exp = A.computed["timer1_period_s"] * 2
        if exp:
            ratio = meas / exp
            ok = abs(ratio - 1) <= 0.03
            row("Scope period", _fmt_t(meas), f"{ex.scope.get('freq_hz', 1/meas):.4g} Hz"
                + (f", duty {ex.scope['duty']*100:.0f}%" if ex.scope.get("duty") is not None else "")
                + (f", {ex.scope['voltage']:.3g} V" if ex.scope.get("voltage") else ""),
                _fmt_t(exp) + (" (pin toggles every " + _fmt_t(exp / 2) + ")" if intent.toggle else ""),
                "ok" if ok else "bad", 161)
            A.computed["ratio"] = ratio
            code_wave = None
            con, prn = f"T{intent.timer}CON", f"PR{intent.timer}"
            if con in code_regs and prn in code_regs:
                cper = timer_period_s(fosc_hz / 2, PRESCALERS[(code_regs[con] >> 4) & 3], code_regs[prn])
                code_wave = 2 * cper
                A.computed["code_timer_period_s"] = cper
                row(f"Code Timer{intent.timer}", _fmt_t(cper), f"{prn}={code_regs[prn]}, 1:{PRESCALERS[(code_regs[con] >> 4) & 3]} "
                    f"@ Fcy {fosc_hz/2/1e6:g} MHz -> toggle gives {_fmt_t(code_wave)} on the pin", _fmt_t(exp),
                    "ok" if abs(code_wave / exp - 1) <= 0.03 else "bad", chipdb.REGISTERS[con].page)
            if not ok:
                if code_wave and abs(meas / code_wave - 1) <= 0.03:
                    A.causes.append(f"The firmware does exactly what the code says: Timer{intent.timer} fires every {_fmt_t(code_wave/2)} "
                                    f"and the ISR toggles the pin, so one full period = 2 x {_fmt_t(code_wave/2)} = {_fmt_t(code_wave)} "
                                    f"- matching the scope. Clock and prescaler are fine; the timer must fire every {_fmt_t(exp/2)}.")
                A.causes += _ratio_causes(ratio, intent, code_regs, code_pr, fosc_hz, A, code_explains=bool(code_wave and abs(meas / code_wave - 1) <= 0.03))
            if ex.scope.get("voltage") and ex.scope["voltage"] < 2.5:
                row("Scope amplitude", f"{ex.scope['voltage']:.3g} V", "logic high below VIH of 3.3 V logic", "~3.3 V", "warn")
        else:
            row("Scope period", _fmt_t(meas), "measured", "state the target in the intent field", "info", 161)
        if ex.scope.get("duty") is not None and intent.toggle and abs(ex.scope["duty"] - 0.5) > 0.1:
            row("Scope duty", f"{ex.scope['duty']*100:.0f}%", "a toggle should give 50%", "50%", "warn")

    # ---- serial terminal
    if ex.serial.get("text"):
        txt = ex.serial["text"]
        junk = sum(1 for ch in txt if not (ch.isprintable() or ch in "\r\n\t") or ch in "\ufffd?" or ord(ch) > 0x7E) / max(1, len(txt))
        A.computed["serial_garbage_ratio"] = junk
        status = "ok" if junk < 0.15 else "bad"
        row("Serial text", repr(txt[:40]) + ("…" if len(txt) > 40 else ""), f"{junk*100:.0f}% unreadable characters",
            "readable ASCII", status, 200)
        if status == "bad":
            A.causes.append("Garbled serial characters: classic baud-rate mismatch (wrong Fcy assumption, BRGH vs U1BRG formula, "
                            "or terminal set to another baud) - framing is consistent but bit timing is wrong.")
            if intent.baud and "U1BRG" in code_regs:
                brgh = (code_regs.get("U1MODE", 0) >> 3) & 1
                b = baud_from_brg(fosc_hz / 2, code_regs["U1BRG"], brgh)
                row("Code U1BRG", str(code_regs["U1BRG"]), f"-> {b:,.0f} baud at Fcy {fosc_hz/2/1e6:g} MHz (BRGH={brgh})",
                    f"{intent.baud:,}", "ok" if abs(b - intent.baud) / intent.baud <= 0.02 else "bad", 200)

    # ---- the user's code vs the image
    for r, v in code_regs.items():
        if r in regs and r in ("T1CON", "T2CON", "T3CON", "PR1", "PR2", "PR3", "U1MODE", "U1BRG", "CLKDIV") and regs[r] != v:
            row(f"code vs chip: {r}", f"0x{regs[r]:04X}", f"code writes 0x{v:04X}", f"0x{v:04X}", "warn",
                PAGES.get(r), "the running chip differs from the code - stale firmware, a later write, or a misread")
    if not A.rows:
        row("-", "-", "nothing decodable in the image", "registers, scope period or serial text", "info")
    # de-duplicate fixes per target, keep the first
    seen = set(); A.fixes = [f for f in A.fixes if not (f.target in seen or seen.add(f.target))]
    return A


def _ratio_causes(ratio: float, intent: Intent, code_regs, code_pr, fosc_hz, A: Analysis, code_explains: bool = False) -> list[str]:
    out = []
    close = lambda x: abs(ratio / x - 1) <= 0.04
    fnosc = code_pr.get("FNOSC")
    if close(2):
        if intent.period_s and not intent.toggle:
            if not code_explains:
                out.append("Measured period is exactly 2x the target. A pin toggled in the timer ISR completes one cycle every TWO "
                       "interrupts, so for a square wave of period T the timer must fire every T/2 (or the timer runs at half speed).")
            spec_half = solve(TimerSpec(timer=intent.timer, period_s=intent.period_s / 2, fosc_hz=fosc_hz))
            con, prn = f"T{intent.timer}CON", f"PR{intent.timer}"
            A.fixes.append(Fix(prn, str(code_regs.get(prn, "?")), str(spec_half.period_reg), f"{prn} = {spec_half.period_reg};",
                               f"interrupt every {_fmt_t(intent.period_s/2)} -> {_fmt_t(intent.period_s)} square wave"))
            if code_regs.get(con) is not None and ((code_regs[con] >> 4) & 3) != spec_half.tckps:
                A.fixes.append(Fix(f"{con}bits.TCKPS", None, str(spec_half.tckps), f"{con}bits.TCKPS = {spec_half.tckps};", "prescaler for the half period"))
        if not code_explains:
            out.append("A 2x error also matches Fosc = 16 MHz instead of 32 MHz: CLKDIV.CPDIV=01, or the code assumes FCY 32 MHz when Fcy is 16 MHz.")
    if close(4):
        out.append("Measured period is 4x too long = Fosc 8 MHz instead of 32 MHz: running on FRC WITHOUT the PLL "
                   f"(FNOSC={fnosc or '?'} - should be FRCPLL, with PLL_96MHZ=ON).")
        A.fixes.append(Fix("#pragma config FNOSC", fnosc, "FRCPLL", "#pragma config FNOSC = FRCPLL", "enable the PLL (Fosc 32 MHz)"))
    if close(8):
        out.append("8x too long: FRCDIV at reset default (RCDIV=001 -> 4 MHz) instead of FRCPLL, or prescaler 1:8 vs 1:1 / 1:64 vs 1:8.")
        if fnosc and fnosc != "FRCPLL":
            A.fixes.append(Fix("#pragma config FNOSC", fnosc, "FRCPLL", "#pragma config FNOSC = FRCPLL", "enable the PLL"))
    if close(0.5):
        out.append("Measured period is half the target: the timer fires twice as fast (PR or prescaler computed for the full "
                   "waveform period while the pin toggles every interrupt).")
    for k in (8, 64, 256, 32):
        if close(1 / k):
            out.append(f"Measured period is 1/{k} of the target: prescaler too small (e.g. TCKPS 1:1 instead of 1:{k}).")
    if not out and not code_explains:
        out.append(f"Measured period is x{ratio:.3g} of the target - not a clean clock/prescaler ratio; check PR, the ISR "
                   "(work inside it, flag cleared late) and FRC tolerance (±2%).")
    return out


# ------------------------------------------------------------------ fix diff
def apply_fixes(code: str, fixes: list[Fix]) -> str:
    """Deterministically patch the user's C code with the computed fixes."""
    out = code
    add: list[str] = []
    for f in fixes:
        if f.target.startswith("#pragma config"):
            key = f.target.split()[-1]
            pat = re.compile(r"(#\s*pragma\s+config\s+" + key + r"\s*=\s*)(\w+)")
            if pat.search(out):
                out = pat.sub(lambda m: m.group(1) + f.new, out, count=1)
            else:
                add.append(f.c_line)
            continue
        if "bits." in f.target:
            reg, fld = f.target.split("bits.")
            pat = re.compile(r"\b" + reg + r"bits\." + fld + r"\s*=\s*[^;]+;")
            if pat.search(out):
                out = pat.sub(f.c_line, out, count=1); continue
            r = chipdb.REGISTERS.get(reg); fo = r.field(fld) if r else None
            pat2 = re.compile(r"\b" + reg + r"\s*=\s*(0[xX][0-9A-Fa-f]+|0[bB][01]+|\d+)(\s*[uUlL]*\s*;)")
            m = pat2.search(out)
            if m and fo:
                v = (int(m.group(1), 0) & ~fo.mask) | ((int(f.new) << fo.lsb) & fo.mask)
                out = out[:m.start()] + f"{reg} = 0x{v:04X};" + out[m.end():]
                continue
        else:
            pat = re.compile(r"\b" + f.target + r"\s*=\s*[^;=]+;")
            if pat.search(out):
                out = pat.sub(f.c_line, out, count=1); continue
        add.append(f.c_line)
    if add:
        block = "    /* ChipLlama reverse-debug fix */\n" + "".join(f"    {l}\n" for l in add if not l.startswith("#"))
        pr = [l for l in add if l.startswith("#")]
        if pr:
            out = "\n".join(pr) + "\n" + out
        if block.count("\n") > 1:
            m = re.search(r"int\s+main\s*\([^)]*\)\s*\{\n?", out)
            out = (out[:m.end()] + block + out[m.end():]) if m else out + "\n" + block
    return out


def fix_diff(code: str, fixes: list[Fix], name: str = "main.c") -> str:
    if not fixes:
        return ""
    if not code.strip():
        return "--- (no code given)\n+++ suggested lines\n" + "".join(f"+{f.c_line}    // {f.reason}\n" for f in fixes)
    new = apply_fixes(code, fixes)
    return "".join(difflib.unified_diff(code.splitlines(True), new.splitlines(True), f"a/{name}", f"b/{name}"))


# ------------------------------------------------------------------ step 3: grounded explanation + report
@dataclass
class VisionReport:
    extraction: Extraction
    intent: Intent
    analysis: Analysis
    root_cause: str
    fix_diff: str
    explanation: str = ""
    verdict: str = ""
    regenerate_request: str = ""
    regenerate_context: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self):
        return {"extraction": self.extraction.to_dict(), "intent": asdict(self.intent),
                "findings": [r.to_dict() for r in self.analysis.rows], "causes": self.analysis.causes,
                "fixes": [f.to_dict() for f in self.analysis.fixes], "clock": self.analysis.clock,
                "computed": self.analysis.computed, "uncertain": self.analysis.uncertain, "explained": self.analysis.explained, "pages": self.analysis.pages, "root_cause": self.root_cause,
                "fix_diff": self.fix_diff, "explanation": self.explanation, "verdict": self.verdict,
                "regenerate_request": self.regenerate_request, "regenerate_context": self.regenerate_context, "notes": self.notes}


def facts_text(ex: Extraction, A: Analysis, intent: Intent) -> str:
    L = [f"Image kind: {ex.kind} (vision confidence {ex.confidence:.2f})", f"Intent: {intent.text or '(none)'}",
         f"Clock: {A.clock.get('source')} -> Fcy {A.clock.get('fcy_hz', 0)/1e6:g} MHz"]
    if A.clock.get("assumption"):
        L.append(f"Clock basis: {A.clock['assumption']}")
    L.append("Findings table (register | value | decoded | expected | status | page):")
    L += [f"- {r.register} | {r.value} | {r.decoded} | {r.expected} | {r.status.upper()} | p.{r.page or '-'}" for r in A.rows]
    if A.causes:
        L.append("Deterministic causes:"); L += [f"- {c}" for c in A.causes]
    if A.uncertain:
        L.append("UNCERTAIN (say so, do not claim a bug or OK):"); L += [f"- {c}" for c in A.uncertain]
    if A.explained:
        L.append("Explained, NOT a bug:"); L += [f"- {c}" for c in A.explained]
    if A.fixes:
        L.append("Computed fix lines:"); L += [f"- {f.c_line}   ({f.reason})" for f in A.fixes]
    return "\n".join(L)


def resolve_device(device, packs_dir: str = "", cache_dir: str = ".cache"):
    if device is None or hasattr(device, "sfr"):
        return device
    try:
        dev = chipdb.get_device(str(device), packs_dir, cache_dir)
    except Exception:
        dev = None
    return dev if dev is not None else str(device).upper()


def vision_debug(images: list[bytes], intent: str = "", code: str = "", model: ChatModel | None = None,
                 fosc_hz: float = 32e6, expected_cosc: int = 1, crystal_hz: float = 8e6,
                 extraction: Extraction | None = None, explain: bool = True, device=None,
                 clock_explicit: bool = False, packs_dir: str = "", cache_dir: str = ".cache",
                 passes: int = 2, progress=None) -> VisionReport:
    notes: list[str] = []
    it = parse_intent(intent)
    device = resolve_device(device, packs_dir, cache_dir)
    ex = extraction
    if ex is None:
        if model is None:
            raise ModelError("a vision model is needed to read the image (set MODEL_API_KEY) - or pass a register dump")
        ex = robust_extract(model, images, intent, getattr(device, "name", device) or "", passes, progress)
        if len(ex.passes) > 1:
            notes.append(f"{len(ex.passes)} independent vision passes; "
                         + (("disagreed on " + ", ".join(ex.candidates)) if ex.candidates else "all readings agree"))
        notes.append(f"Muse vision read the image as '{ex.kind}' (confidence {ex.confidence:.2f}); every number below is "
                     "recomputed by ChipLlama")
    if ex.repaired:
        notes.append("JSON repair: " + "; ".join(ex.repaired))
    if ex.confidence and ex.confidence < 0.5:
        notes.append("low vision confidence - double-check the extracted values against the image")
    if ex.candidates:
        writes = dict(it.writes)
        clean = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", code or "", flags=re.S))
        for m in _LAT_WRITE.finditer(clean):
            writes.setdefault(m.group(1), int(m.group(2), 0) & 0xFFFF)
        notes += _port_consistency(ex, writes, device, port_mask)
    A = analyze(ex, it, code, fosc_hz, expected_cosc, crystal_hz, clock_explicit, device)
    if ex.disputed:
        _demote_disputed(A, ex)
    if not A.clock.get("clock_known") and any(r.register.startswith(("U1BRG", "Timer", "Fcy")) and r.status != "info" for r in A.rows):
        notes.append("clock assumption: " + A.clock.get("assumption", "") + " - add e.g. 'Fcy 4 MHz' to the intent if different")
    elif it.fcy_hz:
        notes.append("clock: " + it.clock_note + " (preferred over the board profile)")
    nbad = sum(r.status == "bad" for r in A.rows)
    warns = [r for r in A.rows if r.status == "warn"]
    if nbad and A.causes:
        root = A.causes[0]
    elif nbad:
        root = next(f"{r.register} = {r.value}: {r.decoded} (expected {r.expected})" for r in A.rows if r.status == "bad")
    elif A.uncertain:
        root = A.uncertain[0]
    elif warns:
        root = f"{warns[0].register} = {warns[0].value}: {warns[0].decoded}" + (f" ({warns[0].note})" if warns[0].note else "")
    elif A.explained:
        root = A.explained[0]
    elif it.text and any(r.status == "ok" for r in A.rows):
        root = "No fault found: the image matches the intent."
    else:
        root = "No rule-based fault found; give an intent (e.g. 'Timer1 10 ms toggle', 'UART 115200')."
    rep = VisionReport(ex, it, A, root, fix_diff(code, A.fixes), notes=notes)
    if A.fixes and code.strip() and not rep.fix_diff:
        rep.fix_diff = fix_diff("", A.fixes)
        notes.append("your code already contains these values, so the chip is running DIFFERENT firmware than this code "
                     "(stale build, wrong project flashed, or the write happens later) - rebuild and reflash")
    if model is not None and explain:
        user = facts_text(ex, A, it) + (f"\n\nUser's code:\n```c\n{code[:12000]}\n```" if code else "")
        try:
            rep.explanation = model.complete([Message("system", EXPLAIN_SYSTEM), Message("user", user)])
        except ModelError as e:
            notes.append(f"model explanation unavailable: {e}")
    if nbad:
        rep.verdict = f"BUG FOUND: {root}"
    elif A.uncertain or warns:
        rep.verdict = f"UNCERTAIN: {root}"
    elif A.explained:
        rep.verdict = f"OK: {root}"
    elif it.text and any(r.status == "ok" for r in A.rows):
        rep.verdict = "OK: matches intent"
    else:
        rep.verdict = "INCONCLUSIVE: " + root
    if A.fixes or nbad:
        rep.regenerate_request = it.text or "Fix the firmware"
        rep.regenerate_context = ("Reverse-debug of the running board found:\n" + "\n".join(f"- {c}" for c in A.causes)
                                  + ("\nRequired changes:\n" + "\n".join(f"- {f.c_line}  ({f.reason})" for f in A.fixes) if A.fixes else ""))
    return rep


def _row_reg(r: Row) -> str:
    return re.split(r"[ .\[(]", r.register, maxsplit=1)[0].upper()


def _demote_disputed(A: Analysis, ex: Extraction):
    """A BUG conclusion that rests solely on a disputed reading becomes UNCERTAIN; values show 'read as X or Y'."""
    for r in A.rows:
        k = _row_reg(r)
        if k in ex.disputed:
            r.value = f"{r.value} (read as {' or '.join(f'0x{v:04X}' for v in ex.disputed[k])})"
            r.note = (r.note + "; " if r.note else "") + ambiguity_note(k, ex.disputed[k]) + " - vision passes disagree"
    bad = [r for r in A.rows if r.status == "bad"]
    if bad and all(_row_reg(r) in ex.disputed for r in bad):
        for r in bad:
            r.status = "warn"
        names = sorted({_row_reg(r) for r in bad})
        A.uncertain[:0] = [f"Depends on a disputed reading ({ambiguity_note(k, ex.disputed[k])}): " + c
                           for c in A.causes for k in names[:1]] or [ambiguity_note(k, ex.disputed[k]) for k in names]
        A.uncertain.append("Re-take the screenshot larger / zoom the SFR window, or type the value into the intent, to decide.")
        A.causes.clear()
        A.fixes.clear()
    elif ex.disputed:
        A.uncertain += [ambiguity_note(k, v) + " (vision passes disagree; not used as sole evidence)" for k, v in ex.disputed.items()]


def format_report(rep: VisionReport) -> str:
    A = rep.analysis
    w = [max(len(r.register) for r in A.rows), max(len(r.value) for r in A.rows)]
    out = [f"Image: {rep.extraction.kind} (confidence {rep.extraction.confidence:.2f})", f"Intent: {rep.intent.text or '-'}"]
    out += [f"pass {p['pass']}: {p['what']}" + (f"  disagree: {p['disagree']}" if p.get("disagree") else "")
            + (f"  reads: {p['reads']}" if p.get("reads") else "") + (f"  vote: {p['voted']}" if p.get("voted") else "")
            for p in rep.extraction.passes]
    out += ["",
           f"{'REGISTER':<{w[0]}}  {'VALUE':<{w[1]}}  STATUS  DECODED  ->  EXPECTED"]
    for r in A.rows:
        out.append(f"{r.register:<{w[0]}}  {r.value:<{w[1]}}  {r.status.upper():<6}  {r.decoded}  ->  {r.expected}"
                   + (f"  [p.{r.page}]" if r.page else ""))
    out += ["", "Root cause:", "  " + rep.root_cause]
    out += [f"  also: {c}" for c in A.causes[1:] + A.uncertain + A.explained if c != rep.root_cause]
    if rep.fix_diff:
        out += ["", "Fix:", rep.fix_diff.rstrip()]
    if rep.explanation:
        out += ["", "Muse Spark explanation:", rep.explanation.strip()]
    out += [f"note: {n}" for n in rep.notes]
    out += ["", "VERDICT: " + rep.verdict]
    return "\n".join(out)
