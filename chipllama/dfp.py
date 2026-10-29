"""Device Family Pack (DFP) selection for -mdfp=.

Rules (Microchip "Introduction to DFPs" + XC32 v5.00 release notes):
* XC8 / XC16 / XC-DSC: -mdfp=<pack>/<xc8|xc16>   (compiler subfolder inside the pack)
* XC32:               -mdfp=<pack>                (pack ROOT, no xc32 suffix)
* XC32 v5+ no longer ships device files: the pack itself must carry the v5 layout
  (xc32/device_files/<DEV>.info). Older packs only have xc32/<DEV>/ + xc32_device.info ->
  v5 fails with "Startup code from DFP has invalid extension" / "Could not open resource file
  xc32/device_files/<DEV>.info". Fix: update the pack in MPLAB X Pack Manager.
* XC32 v4 accepts -mdfp=<pack root> with the old layout (xc32_device.info) or no -mdfp at all.
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path


def compiler_version(path: str | None) -> tuple[int, int]:
    m = re.search(r"v(\d+)\.(\d+)", (path or "").replace("\\", "/"))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _vkey(s: str):
    return tuple(int(x) for x in re.findall(r"\d+", s)) or (0,)


def packs_with_device(device: str, configured: str = "") -> list[Path]:
    """All installed pack version folders containing edc/<DEVICE>.PIC, newest first."""
    from .edc import pack_roots
    dev = device.upper()
    hits = set()
    for root in pack_roots(configured):
        for pat in (os.path.join(root, "*", "*_DFP", "*", "edc", "*.PIC"), os.path.join(root, "*_DFP", "*", "edc", "*.PIC")):
            for p in glob.glob(pat):
                if Path(p).stem.upper() == dev:
                    hits.add(Path(p).parent.parent)
    return sorted(hits, key=lambda p: _vkey(p.name), reverse=True)


def xc32_layout(pack: Path, cpu: str) -> str:
    """'v5' if the pack has xc32/device_files/<cpu>.info, 'v4' if only the older layout, '' if none."""
    x = pack / "xc32"
    if (x / "device_files" / f"{cpu}.info").is_file() or (x / "device_files").is_dir():
        return "v5"
    if (x / cpu).is_dir() or (x / "xc32_device.info").is_file():
        return "v4"
    return ""


def select(comp: str, device: str, compiler_path: str | None, configured: str = "") -> tuple[str | None, str]:
    """(value for -mdfp= or None, warning text or '')."""
    from .edc import compiler_cpu
    packs = packs_with_device(device, configured)
    if comp in ("xc8", "xc16", "xc-dsc"):
        sub = "xc8" if comp == "xc8" else "xc16"
        for p in packs:
            if (p / sub).is_dir():
                return str(p / sub), ""
        return None, ""
    if comp != "xc32":
        return None, ""
    cpu = compiler_cpu(device)
    major = compiler_version(compiler_path)[0]
    if major >= 5:
        for p in packs:
            if xc32_layout(p, cpu) == "v5":
                return str(p), ""
        have = ", ".join(f"{p.parent.name} {p.name}" for p in packs[:2]) or "no pack"
        return (str(packs[0]) if packs else None), (
            f"XC32 v{major} needs a DFP with xc32/device_files/{cpu}.info; installed: {have} (old layout). "
            "Update the pack (e.g. PIC32MX_DFP) in MPLAB X > Tools > Packs, or use XC32 v4.x.")
    for p in packs:
        if xc32_layout(p, cpu):
            return str(p), ""
    return None, ""
