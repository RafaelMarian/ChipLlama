"""Render the synthetic reverse-debug demo images in examples/debug/ (PIL only).

python scripts/make_debug_examples.py
Each image has a matching *.extraction.json (what a perfect vision read returns) so the
deterministic pipeline can be replayed offline:  chipllama debug --image X.png --extraction X.extraction.json ...
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "examples" / "debug"


def font(size, bold=False):
    for p in (["DejaVuSansMono-Bold.ttf", "consolab.ttf"] if bold else ["DejaVuSansMono.ttf", "consola.ttf"]):
        for d in ("/usr/share/fonts/truetype/dejavu/", "C:/Windows/Fonts/", ""):
            try:
                return ImageFont.truetype(d + p, size)
            except OSError:
                pass
    return ImageFont.load_default()


def sfr_window(path: Path, title: str, rows: list[tuple[str, str, int]]):
    """MPLAB X style 'SFRs' watch table: Address | Name | Hex | Binary."""
    W, rh = 900, 30
    H = 92 + rh * (len(rows) + 1) + 20
    im = Image.new("RGB", (W, H), "#f3f3f3"); d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 32], fill="#dfe6ee"); d.text((12, 7), "MPLAB X IDE v6.35 - ChipLlama_demo : default", font=font(15), fill="#222")
    d.rectangle([0, 36, W, 64], fill="#e9e9e9"); d.text((12, 42), title, font=font(15, True), fill="#1a3d6b")
    cols = [(10, "Address"), (110, "Name"), (260, "Hex"), (370, "Decimal"), (480, "Binary")]
    y = 72
    d.rectangle([0, y, W, y + rh], fill="#d6dbe3")
    for x, c in cols:
        d.text((x, y + 6), c, font=font(14, True), fill="#111")
    for i, (name, addr, val) in enumerate(rows):
        y += rh
        d.rectangle([0, y, W, y + rh], fill="#ffffff" if i % 2 else "#f7f9fc")
        b = f"{val:016b}"
        for x, t in zip([c[0] for c in cols], [addr, name, f"0x{val:04X}", str(val), " ".join(b[k:k + 4] for k in range(0, 16, 4))]):
            d.text((x, y + 6), t, font=font(15), fill="#000")
    d.line([0, y + rh, W, y + rh], fill="#bbb")
    im.save(path)


def scope(path: Path, period_ms: float, ms_per_div: float = 10.0, vpp: float = 3.28, duty=0.5):
    W, H = 1000, 620
    gx0, gy0, gw, gh = 40, 70, 800, 480
    im = Image.new("RGB", (W, H), "#101418"); d = ImageDraw.Draw(im)
    d.text((40, 20), "RIGOL DS1054Z   STOP", font=font(20, True), fill="#e8e8e8")
    d.text((520, 22), f"H {ms_per_div:g}ms/div", font=font(18), fill="#ffd400")
    d.rectangle([gx0, gy0, gx0 + gw, gy0 + gh], outline="#555")
    for i in range(1, 10):
        x = gx0 + gw * i / 10
        for y in range(gy0, gy0 + gh, 6):
            d.point((x, y), fill="#444")
    for j in range(1, 8):
        y = gy0 + gh * j / 8
        for x in range(gx0, gx0 + gw, 6):
            d.point((x, y), fill="#444")
    # 1 V/div, 0 V at 6th division from top
    v2y = lambda v: gy0 + gh * 6 / 8 - v * gh / 8
    px_per_ms = gw / (10 * ms_per_div)
    pts = []; t = 0.0; total = 10 * ms_per_div; hi = True; t0 = period_ms * 0.15
    level = lambda tt: vpp if ((tt + t0) % period_ms) < period_ms * duty else 0.0
    for px in range(gw + 1):
        tt = px / px_per_ms
        pts.append((gx0 + px, v2y(level(tt))))
    for a, b in zip(pts, pts[1:]):
        d.line([a, (b[0], a[1])], fill="#f5e10a", width=3)
        if a[1] != b[1]:
            d.line([(b[0], a[1]), b], fill="#f5e10a", width=3)
    d.text((gx0 + 6, v2y(vpp) - 26), "CH1", font=font(16, True), fill="#f5e10a")
    d.text((gx0 + 6, gy0 + gh + 12), "CH1  1.00V/div   DC   RA0", font=font(16), fill="#f5e10a")
    mx = 860
    d.rectangle([mx - 10, gy0, W - 10, gy0 + 300], outline="#555")
    meas = [("Period", f"{period_ms:.2f}ms"), ("Freq", f"{1000/period_ms:.2f}Hz"), ("+Duty", f"{duty*100:.1f}%"), ("Vpp", f"{vpp:.2f}V")]
    for i, (k, v) in enumerate(meas):
        d.text((mx, gy0 + 12 + i * 70), k, font=font(16), fill="#9ad"); d.text((mx, gy0 + 36 + i * 70), v, font=font(18, True), fill="#fff")
    im.save(path)


DEMOS = {
    "sfr_timer1_prescaler_bug": dict(
        intent="Timer1 interrupt every 10 ms, toggle RA0",
        rows=[("OSCCON", "0742", 0x1120), ("CLKDIV", "0744", 0x0100), ("T1CON", "0104", 0x8000), ("PR1", "0102", 0x4E1F),
              ("TMR1", "0100", 0x1A2B), ("IFS0", "0084", 0x0000), ("IEC0", "0094", 0x0008), ("IPC0", "00A4", 0x5444)],
        title="SFRs (Timer1) - halted at main.c:57"),
    "sfr_pll_off": dict(
        intent="Timer1 interrupt every 10 ms, toggle RA0",
        rows=[("OSCCON", "0742", 0x0000), ("CLKDIV", "0744", 0x0100), ("T1CON", "0104", 0x8010), ("PR1", "0102", 0x4E1F),
              ("IEC0", "0094", 0x0008), ("IPC0", "00A4", 0x5444)],
        title="SFRs (Oscillator / Timer1) - halted at main.c:57"),
}
SCOPE = dict(name="scope_20ms_instead_of_10ms", intent="RA0 square wave with 10 ms period (100 Hz)", period_ms=20.0)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, d in DEMOS.items():
        sfr_window(OUT / f"{name}.png", d["title"], [(n, a, v) for n, a, v in d["rows"]])
        ex = {"kind": "registers", "registers": {n: f"0x{v:04X}" for n, _, v in d["rows"]}, "scope": {}, "serial": {},
              "confidence": 0.97, "notes": "synthetic demo image"}
        (OUT / f"{name}.extraction.json").write_text(json.dumps(ex, indent=2) + "\n")
        (OUT / f"{name}.intent.txt").write_text(d["intent"] + "\n")
    scope(OUT / f"{SCOPE['name']}.png", SCOPE["period_ms"])
    (OUT / f"{SCOPE['name']}.extraction.json").write_text(json.dumps(
        {"kind": "scope", "registers": {}, "scope": {"period_ms": 20.0, "freq_hz": 50.0, "duty": 0.5, "voltage": 3.28,
                                                     "channel": "CH1", "timebase": "10ms/div"},
         "serial": {}, "confidence": 0.95, "notes": "synthetic demo image"}, indent=2) + "\n")
    (OUT / f"{SCOPE['name']}.intent.txt").write_text(SCOPE["intent"] + "\n")
    print("wrote", sorted(p.name for p in OUT.glob("*.png")))


if __name__ == "__main__":
    main()
