"""XC16 compile step (xc16-gcc) with graceful skip when the toolchain is not installed."""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .config import Settings


@dataclass
class Diagnostic:
    severity: str
    line: int
    col: int
    message: str


@dataclass
class CompileResult:
    status: str                  # "ok" | "error" | "skipped"
    diagnostics: list[Diagnostic] = field(default_factory=list)
    output: str = ""
    command: list[str] = field(default_factory=list)
    reason: str = ""
    hex_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "skipped")

    def to_dict(self):
        d = asdict(self)
        d["ok"] = self.ok
        return d

    def feedback_text(self) -> str:
        errs = [f"line {d.line}: {d.severity}: {d.message}" for d in self.diagnostics if d.severity == "error"]
        return "\n".join(errs) or self.output[-1500:]


_DIAG = re.compile(r"^(?:[A-Za-z]:)?(?:[^:\n]*?[\\/])?[^:\n]+?:(\d+):(?:(\d+):)?\s*(error|warning|fatal error):\s*(.*)$", re.M)


def parse_diagnostics(text: str) -> list[Diagnostic]:
    out = []
    for m in _DIAG.finditer(text):
        sev = "error" if "error" in m.group(3) else "warning"
        out.append(Diagnostic(sev, int(m.group(1)), int(m.group(2) or 0), m.group(4).strip()))
    return out


def _version_key(p: str):
    m = re.search(r"v(\d+)\.(\d+)", p.replace("\\", "/"))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def find_tool(name: str, configured: str = "") -> str | None:
    """Locate xc16-gcc / xc16-bin2hex: configured path, PATH, then Microchip install dirs."""
    if configured:
        p = Path(configured)
        if p.is_file():
            return str(p)
        w = shutil.which(configured)
        if w:
            return w
    w = shutil.which(name)
    if w:
        return w
    pats = []
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
               "/opt/microchip", "/Applications/microchip"):
        pats.append(os.path.join(pf, "Microchip", "xc16", "v*", "bin", name + ".exe"))
        pats.append(os.path.join(pf, "microchip", "xc16", "v*", "bin", name))
    hits = [h for pat in pats for h in glob.glob(pat)]
    return max(hits, key=_version_key) if hits else None


def compile_c(source: str, settings: Settings, runner=subprocess.run, link: bool = False,
              workdir: str | None = None) -> CompileResult:
    gcc = find_tool("xc16-gcc", settings.xc16_gcc)
    if not gcc:
        return CompileResult("skipped", reason="xc16-gcc not found (install MPLAB XC16 or set XC16_GCC). Compile step skipped; register checker still ran.")
    mcpu = settings.device.upper().replace("PIC", "")
    tmp = workdir or tempfile.mkdtemp(prefix="chipllama_")
    src = Path(tmp) / "main.c"
    src.write_text(source, encoding="utf-8")
    if link:
        out = Path(tmp) / "main.elf"
        cmd = [gcc, f"-mcpu={mcpu}", "-O1", "-Wall", str(src), "-o", str(out)]
    else:
        out = Path(tmp) / "main.o"
        cmd = [gcc, f"-mcpu={mcpu}", "-O1", "-Wall", "-c", str(src), "-o", str(out)]
    try:
        p = runner(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return CompileResult("skipped", command=cmd, reason=f"could not run xc16-gcc: {e}")
    text = (p.stdout or "") + (p.stderr or "")
    diags = parse_diagnostics(text)
    if p.returncode != 0:
        return CompileResult("error", diags, text, cmd)
    res = CompileResult("ok", diags, text, cmd)
    if link:
        b2h = find_tool("xc16-bin2hex", settings.xc16_bin2hex) or str(Path(gcc).with_name("xc16-bin2hex" + Path(gcc).suffix))
        hp = runner([b2h, str(out)], capture_output=True, text=True, timeout=60)
        if hp.returncode == 0:
            res.hex_path = str(Path(tmp) / "main.hex")
        else:
            res.output += "\n" + (hp.stdout or "") + (hp.stderr or "")
    return res
