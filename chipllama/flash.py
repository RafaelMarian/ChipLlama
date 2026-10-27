"""OPTIONAL, off-by-default flashing via MPLAB IPE's ipecmd (PICkit 5 by default).

Command shape (from the `ipecmd -?` help of MPLAB X v6.15 and Microchip's IPE docs):
    ipecmd.exe -P24FJ256GB110 -TPPK5 -F<file.hex> -M -Y -OL
  -P<part>   part name without "PIC"        -TP<short>  tool by short name (PICkit 5 = PK5)
  -TS<ser>   tool by serial number          -F<file>    hex file
  -M         program whole device           -Y          verify
  -OL        release from reset afterwards  -W          power target from the tool
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

from .config import Settings

TOOL_SHORT_NAMES = {"PICKIT5": "PK5", "PICKIT4": "PK4", "PICKIT3": "PK3", "PK5": "PK5", "PK4": "PK4", "PK3": "PK3",
                    "ICD4": "ICD4", "ICD5": "ICD5", "ICD3": "ICD3", "SNAP": "SNAP", "REALICE": "RICE",
                    "PKOB3": "PKOB", "PKOB4": "PKOB4", "ICE4": "ICE4"}


@dataclass
class FlashResult:
    status: str        # "ok" | "error" | "disabled" | "skipped"
    command: list[str]
    output: str = ""
    reason: str = ""

    def to_dict(self):
        return asdict(self)


def find_ipecmd(configured: str = "") -> str | None:
    if configured and Path(configured).is_file():
        return configured
    w = shutil.which("ipecmd") or shutil.which("ipecmd.exe")
    if w:
        return w
    hits = []
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
               "/opt/microchip", "/Applications/microchip"):
        for pat in ("Microchip/MPLABX/v*/mplab_platform/mplab_ipe/ipecmd.exe", "microchip/mplabx/v*/mplab_platform/mplab_ipe/ipecmd.sh",
                    "Microchip/MPLABX/v*/mplab_platform/mplab_ipe/ipecmd.sh"):
            hits += glob.glob(os.path.join(pf, pat))
    return sorted(hits)[-1] if hits else None


def part_name(device: str) -> str:
    d = device.upper()
    return d[3:] if d.startswith("PIC") else d      # PIC24FJ256GB110 -> 24FJ256GB110


def build_command(ipecmd: str, hex_file: str, s: Settings, verify: bool = True) -> list[str]:
    tool = TOOL_SHORT_NAMES.get(s.ipecmd_tool.strip().upper().replace(" ", ""), s.ipecmd_tool.strip().upper())
    cmd = [ipecmd, f"-P{part_name(s.device)}"]
    cmd.append(f"-TS{s.ipecmd_serial}" if s.ipecmd_serial else f"-TP{tool}")
    cmd += [f"-F{hex_file}", "-M"]
    if verify:
        cmd.append("-Y")
    if s.ipecmd_power_target:
        cmd.append("-W")
    cmd.append("-OL")
    return cmd


def flash(hex_file: str, s: Settings, confirmed: bool = False, runner=subprocess.run) -> FlashResult:
    """Flash only if CHIPLLAMA_ENABLE_FLASH=1 AND the caller passes confirmed=True."""
    if not s.enable_flash:
        return FlashResult("disabled", [], reason="Flashing is OFF. Set CHIPLLAMA_ENABLE_FLASH=1 in .env to allow it.")
    if not confirmed:
        return FlashResult("disabled", [], reason="Flash requires explicit confirmation (--yes / confirm=true).")
    if not Path(hex_file).is_file():
        return FlashResult("skipped", [], reason=f"hex file not found: {hex_file}")
    ipe = find_ipecmd(s.ipecmd_path)
    if not ipe:
        return FlashResult("skipped", [], reason="ipecmd not found. Set IPECMD_PATH (see README).")
    cmd = build_command(ipe, str(hex_file), s)
    try:
        p = runner(cmd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        return FlashResult("error", cmd, reason=str(e))
    out = (p.stdout or "") + (p.stderr or "")
    return FlashResult("ok" if p.returncode == 0 else "error", cmd, out,
                       "" if p.returncode == 0 else f"ipecmd exit code {p.returncode}")
