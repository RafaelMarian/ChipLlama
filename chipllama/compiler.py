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


def find_tool(name: str, configured: str = "", compiler: str | None = None) -> str | None:
    """Locate xc8-cc / xc16-gcc / xc32-gcc (+ bin2hex): configured path, PATH, then
    <ProgramFiles>/Microchip/<xc8|xc16|xc32>/v*/bin (newest version wins)."""
    compiler = compiler or name.split("-")[0]
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
        pats.append(os.path.join(pf, "Microchip", compiler, "v*", "bin", name + ".exe"))
        pats.append(os.path.join(pf, "microchip", compiler, "v*", "bin", name))
    hits = [h for pat in pats for h in glob.glob(pat)]
    return max(hits, key=_version_key) if hits else None


# family compiler -> (driver, settings attribute, env var, cpu flag)
COMPILERS = {
    "xc8": ("xc8-cc", "xc8_cc", "XC8_CC", "-mcpu="),
    "xc16": ("xc16-gcc", "xc16_gcc", "XC16_GCC", "-mcpu="),
    "xc32": ("xc32-gcc", "xc32_gcc", "XC32_GCC", "-mprocessor="),
    "xc-dsc": ("xc-dsc-gcc", "xc_dsc_gcc", "XC_DSC_GCC", "-mcpu="),
}


def _dsc_capable(device: str) -> bool:
    return device.upper().startswith("DSPIC")


def compiler_for_device(device: str) -> str:
    from .edc import compiler_for
    return compiler_for(device)


def find_compiler(settings: Settings, device: str | None = None) -> tuple[str, str | None]:
    """(compiler id, path to driver or None) for the configured / given device."""
    device = device or settings.device
    comp = compiler_for_device(device)
    if comp == "xc16" and _dsc_capable(device):
        pref = (getattr(settings, "dspic_compiler", "auto") or "auto").lower()
        if pref in ("auto", "xc-dsc"):
            dsc = find_tool("xc-dsc-gcc", getattr(settings, "xc_dsc_gcc", ""), "xc-dsc")
            if dsc or pref == "xc-dsc":
                return "xc-dsc", dsc
    drv, attr, _, _ = COMPILERS[comp]
    cfg = getattr(settings, attr, "")
    return comp, (find_tool(drv, cfg) if comp == "xc16" else find_tool(drv, cfg, comp))


def _dfp_for(settings: Settings, comp: str, compiler_path: str | None = None) -> str | None:
    """Value for -mdfp= (see dfp.py for the per-compiler rules)."""
    from . import dfp
    try:
        return dfp.select(comp, settings.device, compiler_path, getattr(settings, "mplab_packs_dir", ""))[0]
    except Exception:
        return None


def compile_c(source: str, settings: Settings, runner=subprocess.run, link: bool = False,
              workdir: str | None = None) -> CompileResult:
    comp, gcc = find_compiler(settings)
    drv, _, env, flag = COMPILERS[comp]
    if not gcc:
        return CompileResult("skipped", reason=f"{drv} not found (install MPLAB {comp.upper()} or set {env}). "
                                               "Compile step skipped; checker still ran.")
    from .edc import compiler_cpu
    mcpu = compiler_cpu(settings.device)
    tmp = workdir or tempfile.mkdtemp(prefix="chipllama_")
    src = Path(tmp) / "main.c"
    src.write_text(source, encoding="utf-8")
    if comp not in ("xc16", "xc-dsc"):
        return _compile_other(comp, gcc, flag + mcpu, src, Path(tmp), settings, runner, link)
    from .dfp import compiler_version
    dfp = _dfp_for(settings, comp, gcc) if (comp == "xc-dsc" and compiler_version(gcc) >= (3, 30)) else None
    extra = [f"-mdfp={dfp}"] if dfp else []
    if link:
        out = Path(tmp) / "main.elf"
        cmd = [gcc, f"-mcpu={mcpu}", *extra, "-O1", "-Wall", str(src), "-o", str(out)]
    else:
        out = Path(tmp) / "main.o"
        cmd = [gcc, f"-mcpu={mcpu}", *extra, "-O1", "-Wall", "-c", str(src), "-o", str(out)]
    try:
        p = runner(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return CompileResult("skipped", command=cmd, reason=f"could not run {Path(gcc).name}: {e}")
    text = (p.stdout or "") + (p.stderr or "")
    diags = parse_diagnostics(text)
    if p.returncode != 0:
        return CompileResult("error", diags, text, cmd)
    res = CompileResult("ok", diags, text, cmd)
    if link:
        b2n = "xc-dsc-bin2hex" if comp == "xc-dsc" else "xc16-bin2hex"
        b2h = (find_tool(b2n, settings.xc16_bin2hex) if comp == "xc16" else None) or str(Path(gcc).with_name(b2n + Path(gcc).suffix))
        hp = runner([b2h, str(out)], capture_output=True, text=True, timeout=60)
        if hp.returncode == 0:
            res.hex_path = str(Path(tmp) / "main.hex")
        else:
            res.output += "\n" + (hp.stdout or "") + (hp.stderr or "")
    return res


def _compile_other(comp, drv, cpu, src: Path, tmp: Path, settings, runner, link) -> CompileResult:
    """XC8 (xc8-cc, output .p1 / .hex) and XC32 (xc32-gcc + xc32-bin2hex). Not exercised on hardware yet."""
    dfp = _dfp_for(settings, comp, drv)
    extra = [f"-mdfp={dfp}"] if dfp else []
    if comp == "xc8":
        out = tmp / ("main.hex" if link else "main.p1")
        cmd = [drv, cpu, *extra, "-O1", *([] if link else ["-c"]), str(src), "-o", str(out)]
    else:
        out = tmp / ("main.elf" if link else "main.o")
        cmd = [drv, cpu, *extra, "-O1", "-Wall", *([] if link else ["-c"]), str(src), "-o", str(out)]
    try:
        p = runner(cmd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        return CompileResult("skipped", command=cmd, reason=f"could not run {Path(drv).name}: {e}")
    text = (p.stdout or "") + (p.stderr or "")
    diags = parse_diagnostics(text)
    if p.returncode != 0:
        return CompileResult("error", diags, text, cmd)
    res = CompileResult("ok", diags, text, cmd)
    if link:
        if comp == "xc8":
            res.hex_path = str(out)
        else:
            b2h = str(Path(drv).with_name("xc32-bin2hex" + Path(drv).suffix))
            hp = runner([b2h, str(out)], capture_output=True, text=True, timeout=60)
            if hp.returncode == 0:
                res.hex_path = str(tmp / "main.hex")
    return res
