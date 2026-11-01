"""Build the competition demo video (docs/demo.mp4) from REAL project assets.

Beats: title -> problem -> Generate screenshot -> live `generate` terminal
scroll -> verified code glimpse -> Audit screenshot + findings -> Blocks /
Explain / Pins screenshots -> reverse-debug (real SFR image + decoded table)
-> closing card. Captions throughout, no voiceover.

All footage is genuine: dashboard screenshots from docs/screenshots, a
captured live `chipllama generate` run, and offline `audit` / `debug
--extraction` output re-captured by this script (no API key needed for those).

Usage (Windows PowerShell):
    .venv\\Scripts\\pip.exe install imageio imageio-ffmpeg   # once (build deps only, not shipped)
    .venv\\Scripts\\chipllama.exe generate "Timer1 interrupt every 10 ms, priority 5" --out .cache\\video\\main.c > .cache\\video\\term_generate.txt
    .venv\\Scripts\\python.exe scripts\\make_demo_video.py    # writes docs/demo.mp4 (untracked)

1920x1080 @ 30 fps, H.264, ~2 minutes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "video"
W, H, FPS = 1920, 1080, 30
BG = (13, 17, 23)
FG = (230, 237, 243)
GREEN = (126, 231, 135)
RED = (255, 123, 114)
GRAY = (139, 148, 158)
ACCENT = (255, 166, 87)

CAPTURE_CMDS = {
    "term_audit.txt": ["audit", "examples/audit/blinky_buggy"],
    "term_debug.txt": ["debug", "--image", "examples/debug/sfr_timer1_prescaler_bug.png",
                       "--extraction", "examples/debug/sfr_timer1_prescaler_bug.extraction.json",
                       "--intent", "Timer1 interrupt every 10 ms, toggle RA0", "--no-model"],
}


def fonts():
    from PIL import ImageFont

    def load(names, size):
        for n in names:
            for d in (r"C:\Windows\Fonts", "/usr/share/fonts/truetype/dejavu"):
                p = Path(d) / n
                if p.is_file():
                    return ImageFont.truetype(str(p), size)
        return ImageFont.load_default()

    mono = lambda s: load(["consola.ttf", "DejaVuSansMono.ttf"], s)  # noqa: E731
    sans = lambda s: load(["arial.ttf", "DejaVuSans.ttf"], s)  # noqa: E731
    sans_b = lambda s: load(["arialbd.ttf", "DejaVuSans-Bold.ttf"], s)  # noqa: E731
    return mono, sans, sans_b


def capture_offline() -> None:
    import os

    CACHE.mkdir(parents=True, exist_ok=True)
    exe = ROOT / ".venv" / "Scripts" / "chipllama.exe"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for name, args in CAPTURE_CMDS.items():
        r = subprocess.run([str(exe), *args], capture_output=True, cwd=ROOT, env=env)
        text = (r.stdout or r.stderr).decode("utf-8", errors="replace")
        (CACHE / name).write_text(text, encoding="utf-8")
    need = ["term_generate.txt", "main.c"]
    missing = [n for n in need if not (CACHE / n).is_file()]
    if missing:
        sys.exit(f"missing live footage {missing}: run the generate command from the docstring first")


def read_lines(name: str, width: int) -> list[str]:
    out = []
    for raw in (CACHE / name).read_text(encoding="utf-8-sig").splitlines():
        s = raw.rstrip()
        if s.startswith(("At line:", "CategoryInfo", "+ ", "    + ")):
            continue
        out.append(s[:width] + ("..." if len(s) > width else ""))
    while out and not out[0].strip():
        out.pop(0)
    return out


def text_center(draw, cx, y, s, font, fill):
    from PIL import ImageDraw  # noqa: F401
    l, t, r, b = draw.textbbox((0, 0), s, font=font)
    draw.text((cx - (r - l) / 2, y), s, font=font, fill=fill)


def with_caption(img, lines: list[str], sans) -> None:
    from PIL import Image, ImageDraw

    bar_h = 60 + 52 * len(lines)
    bar = Image.new("RGBA", (W, bar_h), (0, 0, 0, 200))
    img.paste(bar, (0, H - bar_h), bar)
    d = ImageDraw.Draw(img)
    for i, s in enumerate(lines):
        text_center(d, W / 2, H - bar_h + 26 + 52 * i, s, sans(34), FG)


def card_frame(title: str, rows: list[str], sub: str, mono, sans, sans_b):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.line([(W / 2 - 260, 330), (W / 2 + 260, 330)], fill=ACCENT, width=6)
    text_center(d, W / 2, 170, title, sans_b(120), FG)
    for i, s in enumerate(rows):
        text_center(d, W / 2, 400 + 70 * i, s, sans(44), FG)
    text_center(d, W / 2, H - 170, sub, mono(30), GRAY)
    return img


def still_frame(png: Path, zoom: float, caption: list[str], sans):
    from PIL import Image

    src = Image.open(png).convert("RGB")
    zw, zh = int(src.width / zoom), int(src.height / zoom)
    x, y = (src.width - zw) // 2, (src.height - zh) // 2
    img = src.crop((x, y, x + zw, y + zh)).resize((W, H), Image.LANCZOS)
    with_caption(img, caption, sans)
    return img


def term_color(s: str):
    u = s.upper()
    if any(k in u for k in ("VERIFIED", "CHECKER=OK", "0 ERRORS", "COMPILE=OK", "STATUS", "ROOT CAUSE")):
        return GREEN
    if any(k in u for k in ("=FAIL", "FAILED", "ERROR", "BAD", "15/100")):
        return RED
    if "WARNING" in u:
        return ACCENT
    if s.startswith(("note:", " ", "#", "---", "+++", "@@")):
        return GRAY
    return FG


def term_frame(lines: list[str], shown: int, rows: int, caption: list[str], mono, sans, code_mode=False):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    vis = lines[max(0, shown - rows):shown]
    y = 90
    for s in vis:
        if code_mode:
            t = s.strip()
            c = GRAY if t.startswith(("//", "/*", "*", "*/")) else (ACCENT if t.startswith("#") else FG)
        else:
            c = term_color(s)
        d.text((90, y), s, font=mono(26), fill=c)
        y += 36
    with_caption(img, caption, sans)
    return img


def segment(frames_fn, seconds: float) -> tuple:
    return (frames_fn, int(seconds * FPS))


def main() -> None:
    import imageio.v2 as imageio
    import numpy as np

    capture_offline()
    mono, sans, sans_b = fonts()
    gen = read_lines("term_generate.txt", 106)
    gen = [l for l in gen if "device data:" not in l][:26]
    code = (CACHE / "main.c").read_text(encoding="utf-8-sig").splitlines()[:40]
    audit = read_lines("term_audit.txt", 106)[:30]
    debug = read_lines("term_debug.txt", 100)[:24]

    shots = ROOT / "docs" / "screenshots"
    dbg_img = ROOT / "examples" / "debug" / "sfr_timer1_prescaler_bug.png"

    segs = [
        segment(lambda t: card_frame("ChipLlama", ["The PIC firmware copilot", "that proves its code is right"],
                                      "Meta Global AI Developer Hackathon  -  Muse Spark + XC16 + PIC", mono, sans, sans_b), 7),
        segment(lambda t: card_frame("Guessing is not engineering", ['A chatbot says "probably this register value".',
                                      "ChipLlama replays, compiles and checks -", "then marks the code VERIFIED."],
                                      "deterministic solver  +  register emulator  +  real compiler", mono, sans, sans_b), 9),
        segment(lambda t: still_frame(shots / "generate.png", 1.0 + 0.06 * t,
                                      ["Plain English in:  Timer1 interrupt every 10 ms, priority 5"], sans), 11),
        segment(lambda t: term_frame(gen, 1 + int(t * (len(gen) + 8)), 20,
                                      ["Muse writes C  -  XC16 compiles  -  the checker verifies every register"], mono, sans), 18),
        segment(lambda t: term_frame(code, 1 + int(t * (len(code) + 10)), 22,
                                      ["Full config-bit block, computed PR1 = 19999, priority-5 ISR"], mono, sans, True), 10),
        segment(lambda t: still_frame(shots / "audit.png", 1.0 + 0.06 * t,
                                      ["Audit scores code you already have, with datasheet pages"], sans), 10),
        segment(lambda t: term_frame(audit, 1 + int(t * (len(audit) + 8)), 20,
                                      ["blinky_buggy: 15/100  ->  7 one-click fixes  ->  100/100"], mono, sans), 12),
        segment(lambda t: still_frame(shots / "blocks.png", 1.0 + 0.06 * t,
                                      ["Blocks: visual programming, same checker, VERIFIED badge"], sans), 7),
        segment(lambda t: still_frame(shots / "explain.png", 1.0 + 0.06 * t,
                                      ["Explain: every register access decoded and cited"], sans), 7),
        segment(lambda t: still_frame(shots / "pins.png", 1.0 + 0.06 * t,
                                      ["Pin planner: instant PPS conflicts plus init code"], sans), 9),
        segment(lambda t: still_frame(dbg_img, 1.0 + 0.06 * t,
                                      ["Reverse debug: drop an SFR screenshot..."], sans), 7),
        segment(lambda t: term_frame(debug, 1 + int(t * (len(debug) + 6)), 20,
                                      ["...vision reads it, ChipLlama recomputes every number"], mono, sans), 9),
        segment(lambda t: card_frame("Prove it, don't prompt it", ["Muse Spark  -  XC16  -  PIC24 / dsPIC33 / PIC18 / PIC32",
                                      "github.com/RafaelMarian/ChipLlama"],
                                      "Built Oct 27 - Nov 4, 2026  -  MIT", mono, sans, sans_b), 9),
    ]

    out = ROOT / "docs" / "demo.mp4"
    PS = sys.argv[sys.argv.index("--shots") + 1].split(",") if "--shots" in sys.argv else None
    writer = None if PS else imageio.get_writer(str(out), fps=FPS, codec="libx264", quality=8,
                                                macro_block_size=None, ffmpeg_params=["-pix_fmt", "yuv420p"])
    try:
        tail: list = []
        for si, (fn, n) in enumerate(segs):
            for i in range(n):
                img = fn(i / max(1, n - 1))
                if PS is not None:
                    continue
                if tail and i < 15:
                    from PIL import Image

                    img = Image.blend(tail[i - 15], img, (i + 1) / 16)
                writer.append_data(np.asarray(img.convert("RGB")))
            if PS is None:
                from PIL import Image  # noqa: F401

                tail = [fn(1.0) for _ in range(15)]
        if PS is not None:  # --shots i,j: dump mid-segment stills for review instead of rendering
            for s in PS:
                fn, n = segs[int(s)]
                fn(0.5).save(CACHE / f"shot_{s}.png")
                print("wrote", CACHE / f"shot_{s}.png")
    finally:
        if writer is not None:
            writer.close()
    if PS is None:
        print("wrote", out, f"({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
