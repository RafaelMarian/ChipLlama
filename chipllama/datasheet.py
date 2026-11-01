"""Datasheet page images (DS39897C) for multimodal prompts.

The PDF is NOT bundled (Microchip copyright) - place your copy at DATASHEET_PDF.
Rendering uses poppler's `pdftoppm` if available, otherwise PyMuPDF (`pip install pymupdf`).
For this document the PDF page index equals the printed datasheet page number
(verified for the timer/interrupt pages, 84-169).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import chipdb
from .config import Settings, ROOT

PAGE_OFFSET = 0   # printed page N == PDF page N + PAGE_OFFSET (verified = 0 for DS39897C pp.84-169)


def pages_for_spec(spec) -> list[int]:
    """Pick the datasheet pages relevant to a TimerSpec (register tables first)."""
    pages: list[int] = []
    n = spec.base if hasattr(spec, "base") else spec.timer
    if n == 1:
        pages += chipdb.PAGES["timer1"]
    elif n in (2, 4):
        pages += chipdb.PAGES["timer2"]
    else:
        pages += chipdb.PAGES["timer3"]
    if spec.interrupt:
        irq = n + 1 if getattr(spec, "bits32", False) else spec.timer
        pages += {1: [84, 90, 97], 2: [84, 90, 98], 3: [84, 90, 99], 4: [85, 91, 103], 5: [85, 91, 104]}[irq]
    if getattr(spec, "gated", False):
        pages += [142, 143] if n > 1 else [142]
    return list(dict.fromkeys(pages))


def pages_for_registers(names: list[str]) -> list[int]:
    return list(dict.fromkeys(chipdb.PAGE_OF_REGISTER[n] for n in names if n in chipdb.PAGE_OF_REGISTER))


def render_page(pdf: Path, page: int, out_dir: Path, dpi: int = 110) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"p{page:03d}.png"
    if target.is_file():
        return target
    pp = page + PAGE_OFFSET
    if shutil.which("pdftoppm"):
        base = out_dir / f"_p{page:03d}"
        r = subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(pp), "-l", str(pp), "-png", "-singlefile", str(pdf), str(base)],
                           capture_output=True)
        if r.returncode == 0 and base.with_suffix(".png").is_file():
            base.with_suffix(".png").replace(target)
            return target
    try:
        import fitz  # PyMuPDF (optional)
        with fitz.open(str(pdf)) as doc:
            doc[pp - 1].get_pixmap(dpi=dpi).save(str(target))
        return target
    except Exception:
        return None


def load_page_images(pages: list[int], s: Settings) -> tuple[list[tuple[int, bytes]], str]:
    """Return ([(page, png_bytes)], note). Never raises: missing datasheet -> text-only mode."""
    pdf = Path(s.datasheet_pdf)
    if not pdf.is_absolute():
        pdf = ROOT / pdf if (ROOT / pdf).is_file() else Path.cwd() / pdf
    if not pdf.is_file():
        return [], f"datasheet PDF not found at {s.datasheet_pdf}; running text-only (built-in register table)"
    out = Path(s.cache_dir) / "pages"
    imgs = []
    for p in pages:
        f = render_page(pdf, p, out)
        if f:
            imgs.append((p, f.read_bytes()))
    if not imgs:
        return [], "could not render datasheet pages (install poppler `pdftoppm` or `pip install pymupdf`)"
    return imgs, f"attached {len(imgs)} datasheet page image(s): " + ", ".join(f"p{p}" for p, _ in imgs)
