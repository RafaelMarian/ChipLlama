"""Overlay presenter captions on a screen recording (e.g. the OBS demo video).

Draws a title strip, timed lower-third captions and an end card with PIL,
re-encodes video (original fps/resolution) and muxes the original audio
track back in. The input file is never modified.

Usage (Windows PowerShell, from repo root):
    .venv\\Scripts\\python.exe scripts\\caption_video.py --in "C:\\Users\\user\\Videos\\2026-11-01 13-51-14.mp4" --out "C:\\Users\\user\\Videos\\ChipLlama_demo_captioned.mp4"

Captions live in CAPTIONS below as (start_s, end_s, [lines]); ENDCARD is the
closing panel. Adjust times/text for a different recording.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SYS = Path(__file__).resolve().parent
sys.path.insert(0, str(SYS))
import make_demo_video as M  # noqa: E402  (reuse caption primitives)

TITLE = (0.0, 5.0, ["ChipLlama  -  the PIC firmware copilot that proves its code is right"])
CAPTIONS: list[tuple[float, float, list[str]]] = [
    (0.0, 9.0, ["UART1 115200: attempt 1 fails the register checker - U1BRG off by one"]),
    (9.0, 14.5, ["Self-correction: errors + datasheet pages fed back, attempt 2 VERIFIED"]),
    (14.5, 27.0, ["Next request, live: I2C1 master at 400 kHz reads a sensor"]),
    (27.0, 55.0, ["Real API call in progress - Muse Spark is writing the driver"]),
    (55.0, 66.0, ["VERIFIED - registers plus compile. Export, download, or flash"]),
    (66.0, 80.0, ["Blocks: UART + ADC as visual blocks becomes deterministic, verified C"]),
    (80.0, 100.0, ["Circuit: live ERC - LED current, pull-ups, power, every rule cited"]),
    (100.0, 131.0, ["One click: HAL-style MPLAB X project, .hex, BOM with KiCad footprints"]),
    (131.0, 143.0, ["Audit: pasted code scores 100/100, Muse adds a marked second opinion"]),
    (143.0, 151.0, ["Explain mode: every line decoded against the datasheet"]),
    (151.0, 154.5, ["31 lines annotated - register meanings plus page numbers"]),
    (154.5, 166.0, ["Reverse debug: SFR screenshot in, vision transcribes it twice"]),
    (166.0, 169.0, ["All 8 values agree - deterministic decode, no guessing"]),
    (169.0, 173.0, ["Pin planner: PPS conflicts, ready init code, power estimate"]),
]
ENDCARD: tuple[float, float, list[str]] = (
    173.0, 1e9,
    ["ChipLlama", "Muse Spark  -  XC16  -  PIC24", "github.com/RafaelMarian/ChipLlama"],
)


def overlay(frame, t: float, mono, sans, sans_b):
    from PIL import Image, ImageDraw

    img = Image.fromarray(frame).convert("RGB")
    W, H = img.size
    if TITLE[0] <= t < TITLE[1]:
        d = ImageDraw.Draw(img, "RGBA")
        d.rectangle([0, 100, W, 196], fill=(0, 0, 0, 210))
        d2 = ImageDraw.Draw(img)
        l, tp, r, b = d2.textbbox((0, 0), TITLE[2][0], font=sans_b(40))
        d2.text(((W - (r - l)) / 2, 128), TITLE[2][0], font=sans_b(40), fill=M.FG)
    for s, e, lines in CAPTIONS:
        if s <= t < e:
            M.W, M.H = W, H
            M.with_caption(img, lines, sans)
    s, e, lines = ENDCARD
    if s <= t < e:
        d = ImageDraw.Draw(img, "RGBA")
        d.rectangle([W / 2 - 520, H / 2 - 170, W / 2 + 520, H / 2 + 170], fill=(0, 0, 0, 215))
        d2 = ImageDraw.Draw(img)
        for i, (txt, fnt, sz) in enumerate([(lines[0], sans_b, 92), (lines[1], sans, 40), (lines[2], mono, 34)]):
            f = fnt(sz)
            l, tp, r, b = d2.textbbox((0, 0), txt, font=f)
            d2.text(((W - (r - l)) / 2, H / 2 - 130 + i * 96), txt, font=f, fill=M.FG)
    import numpy as np

    return np.asarray(img)


def main() -> None:
    import imageio.v2 as imageio

    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--shots", default="")
    a = ap.parse_args()
    mono, sans, sans_b = M.fonts()
    reader = imageio.get_reader(a.src)
    meta = reader.get_meta_data()
    fps = meta.get("fps", 30)
    if a.shots:
        for ts in a.shots.split(","):
            i = int(float(ts) * fps)
            Image = __import__("PIL.Image", fromlist=["Image"])
            Image.fromarray(overlay(reader.get_data(i), float(ts), mono, sans, sans_b)).save(
                M.ROOT / ".cache" / "vid" / f"cap_{ts}.png")
            print("wrote cap", ts)
        return
    tmp = Path(a.dst).with_suffix(".video.mp4")
    writer = imageio.get_writer(str(tmp), fps=fps, codec="libx264", quality=8,
                                macro_block_size=None, ffmpeg_params=["-pix_fmt", "yuv420p"])
    try:
        for i, frame in enumerate(reader):
            writer.append_data(overlay(frame, i / fps, mono, sans, sans_b))
    finally:
        writer.close()
    import imageio_ffmpeg

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(tmp), "-i", a.src,
                    "-map", "0:v", "-map", "1:a?", "-c:v", "copy", "-c:a", "copy",
                    "-shortest", a.dst], check=True)
    tmp.unlink()
    print("wrote", a.dst, f"({Path(a.dst).stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
