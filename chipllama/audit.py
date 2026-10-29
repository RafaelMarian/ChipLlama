"""Static audit of an existing PIC24FJ256GB110 C project (single file, folder or MPLAB X project).

Deterministic rules (no AI): configuration words, ICS/JTAG/WDT, clock vs FCY, PPS, analog/digital
(AD1PCFG), TRIS/LAT, interrupts (names, flags, enables without a handler, PSV attribute) and volatile.
Each finding carries severity, file:line, datasheet pages (DS39897C) and, when safe, a line-level fix.
"""
from __future__ import annotations

import difflib
import io
import re
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import chipdb, irq
from .board import ICS_PAIRS
from .cemu import emulate, isr_clears_flag, strip_comments, split_functions
from .uart import UART_RX, UART_TX_FN, OUTPUT_RP_MAX, rpor_field
from .spi import OUT_FN as SPI_OUT_FN

CW_PAGES = [288, 290, 291]
JTAG_PINS = {"RA0": "TMS", "RA1": "TCK", "RA4": "TDI", "RA5": "TDO"}
SEV_COST = {"error": 15, "warning": 5, "info": 1}
SKIP_DIRS = {"build", "dist", "debug", ".git", ".generated_files", "__pycache__", "node_modules"}
RCDIV = {0: 1, 1: 2, 2: 4, 3: 8, 4: 16, 5: 32, 6: 64, 7: 256}


@dataclass
class Fix:
    file: str
    line: int                 # 1-based; replace this line, or insert before it when `insert` is True
    new: str
    insert: bool = False


@dataclass
class AuditFinding:
    rule: str
    severity: str
    message: str
    file: str = ""
    line: int = 0
    pages: list[int] = field(default_factory=list)
    fix: Fix | None = None
    hint: str = ""

    def __post_init__(self):
        self.pages = sorted({int(p) for p in self.pages if p})

    def to_dict(self):
        d = asdict(self)
        d["fixable"] = self.fix is not None
        return d


@dataclass
class AuditReport:
    files: dict[str, str]
    findings: list[AuditFinding] = field(default_factory=list)
    device: str = "PIC24FJ256GB110"
    project: str = "files"
    fcy: float | None = None
    explanation: str = ""
    compile: dict | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        return max(0, 100 - sum(SEV_COST.get(f.severity, 0) for f in self.findings))

    def counts(self):
        return {s: sum(f.severity == s for f in self.findings) for s in ("error", "warning", "info")}

    def patched(self) -> dict[str, str]:
        return apply_fixes(self.files, [f.fix for f in self.findings if f.fix])

    def diff(self) -> str:
        new = self.patched()
        out = []
        for name, old in self.files.items():
            if new[name] != old:
                out += difflib.unified_diff(old.splitlines(True), new[name].splitlines(True), "a/" + name, "b/" + name)
        return "".join(out)

    def to_dict(self):
        return {"device": self.device, "project": self.project, "score": self.score, "counts": self.counts(),
                "fcy": self.fcy, "files": list(self.files), "findings": [f.to_dict() for f in self.findings],
                "diff": self.diff(), "explanation": self.explanation, "compile": self.compile, "notes": self.notes}


# ------------------------------------------------------------------ loading
def load_path(path: str | Path) -> tuple[dict[str, str], str, str]:
    """-> ({relative name: text}, project kind, device)."""
    p = Path(path)
    if p.is_file():
        return {p.name: p.read_text(encoding="utf-8", errors="replace")}, "file", "PIC24FJ256GB110"
    files, kind, device = {}, "folder", "PIC24FJ256GB110"
    for f in sorted(p.rglob("*")):
        rel = f.relative_to(p)
        if any(part.lower() in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if f.is_file() and f.suffix.lower() in (".c", ".h"):
            files[rel.as_posix()] = f.read_text(encoding="utf-8", errors="replace")
    cfg = p / "nbproject" / "configurations.xml"
    if cfg.is_file():
        kind = "mplabx"
        m = re.search(r"<targetDevice>\s*([\w-]+)\s*</targetDevice>", cfg.read_text(encoding="utf-8", errors="replace"))
        if m:
            device = m.group(1).upper()
    return files, kind, device


def load_zip(data: bytes) -> dict[str, str]:
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if n.lower().endswith((".c", ".h")) and not any(s in n.lower().split("/") for s in SKIP_DIRS):
                files[n] = z.read(n).decode("utf-8", errors="replace")
        xml = [n for n in z.namelist() if n.endswith("nbproject/configurations.xml")]
        if xml:
            files["__device__"] = (re.search(r"<targetDevice>\s*([\w-]+)", z.read(xml[0]).decode("utf-8", "replace"))
                                   or [None, ""])[1]
    return files


# ------------------------------------------------------------------ helpers
def apply_fixes(files: dict[str, str], fixes: list[Fix]) -> dict[str, str]:
    out = dict(files)
    by: dict[str, list[Fix]] = {}
    for fx in fixes:
        by.setdefault(fx.file, []).append(fx)
    for name, fl in by.items():
        lines = out[name].split("\n")
        seen = set()
        for fx in sorted(fl, key=lambda f: (f.line, not f.insert), reverse=True):
            key = (fx.line, fx.insert, fx.new)
            if key in seen or not 1 <= fx.line <= len(lines) + 1:
                continue
            seen.add(key)
            if fx.insert:
                lines.insert(fx.line - 1, fx.new)
            else:
                lines[fx.line - 1] = fx.new
        out[name] = "\n".join(lines)
    return out


class Src:
    def __init__(self, files: dict[str, str]):
        self.files = {k: v for k, v in files.items() if not k.startswith("__")}
        self.clean = {k: _blank_comments(v) for k, v in self.files.items()}

    def find(self, pattern: str, flags=0):
        """Yield (file, line, match, line_text) on comment-stripped text."""
        rx = re.compile(pattern, flags)
        for name, txt in self.clean.items():
            for i, ln in enumerate(txt.split("\n"), 1):
                for m in rx.finditer(ln):
                    yield name, i, m, self.files[name].split("\n")[i - 1]

    def first(self, pattern: str, flags=0):
        return next(self.find(pattern, flags), None)

    def any(self, pattern: str, flags=0) -> bool:
        return self.first(pattern, flags) is not None

    def main_file(self) -> str:
        hit = self.first(r"\bint\s+main\s*\(")
        return hit[0] if hit else next((k for k in self.files if k.endswith(".c")), next(iter(self.files), ""))


def _blank_comments(src: str) -> str:
    """Replace comments by spaces but keep line structure."""
    def rep(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    return re.sub(r"/\*.*?\*/|//[^\n]*", rep, src, flags=re.S)


def _indent(s: str) -> str:
    return re.match(r"\s*", s).group(0)


def parse_config(src: Src) -> tuple[dict[str, tuple[str, str, int, str]], bool]:
    """-> ({SETTING: (value, file, line, text)}, legacy_macros_used)."""
    cfg = {}
    for name, ln, m, text in src.find(r"#\s*pragma\s+config\s+(.+)$"):
        for part in m.group(1).split(","):
            kv = part.split("=")
            if len(kv) == 2:
                cfg[kv[0].strip().upper()] = (kv[1].strip().upper(), name, ln, text)
    legacy = False
    for name, ln, m, text in src.find(r"\b_CONFIG[123]\s*\((.*)\)"):
        legacy = True
        for tok in re.split(r"[&|\s()]+", m.group(1)):
            mm = re.match(r"([A-Z0-9]+)_([A-Za-z0-9]+)$", tok)
            if mm:
                val = mm.group(2).upper()
                val = {"PGX1": "PGx1", "PGX2": "PGx2", "PGX3": "PGx3"}.get(val, val)
                cfg[mm.group(1)] = (val, name, ln, text)
    for k, v in list(cfg.items()):
        if k == "ICS":
            cfg[k] = (v[0].replace("PGX", "PGx"),) + v[1:]
    return cfg, legacy


def fcy_from_config(cfg, regs, crystal_hz: float = 8e6) -> tuple[float | None, str]:
    fnosc = cfg.get("FNOSC", ("FRCDIV",))[0]
    rcdiv = (regs.get("CLKDIV", 0x0100) >> 8) & 7
    cpdiv = (regs.get("CLKDIV", 0) >> 6) & 3
    if fnosc == "FRCPLL":
        fosc, why = 32e6 / (1 << cpdiv), f"FRC+PLL 96 MHz/3, CPDIV={cpdiv}"
    elif fnosc == "PRIPLL":
        fosc, why = 32e6 / (1 << cpdiv), f"primary+PLL, CPDIV={cpdiv}"
    elif fnosc in ("FRC",):
        fosc, why = 8e6, "FRC 8 MHz"
    elif fnosc == "FRCDIV":
        fosc, why = 8e6 / RCDIV[rcdiv], f"FRC/{RCDIV[rcdiv]} (RCDIV={rcdiv})"
    elif fnosc == "LPRC":
        fosc, why = 31e3, "LPRC 31 kHz"
    elif fnosc == "PRI":
        fosc, why = crystal_hz, f"primary oscillator {crystal_hz/1e6:g} MHz (assumed)"
    else:
        return None, f"FNOSC={fnosc}"
    return fosc / 2, why


# ------------------------------------------------------------------ rules
def audit_files(files: dict[str, str], *, ics: str = "PGx2", crystal_hz: float = 8e6,
                device: str = "PIC24FJ256GB110", project: str = "files") -> AuditReport:
    device = files.get("__device__") or device
    src = Src(files)
    rep = AuditReport(files=src.files, device=device, project=project)
    F = rep.findings
    if not src.files:
        rep.notes.append("no .c/.h files found")
        return rep
    if device.upper() != "PIC24FJ256GB110":
        F.append(AuditFinding("DEVICE", "info", f"Project targets {device}; rules are tuned for the PIC24FJ256GB110 "
                                               f"(pin/PPS/AN maps may differ)."))
    mainf = src.main_file()
    csrc = "\n".join(v for k, v in src.files.items() if k.endswith(".c")) or "\n".join(src.files.values())
    emu = emulate(csrc)
    regs = emu.regs
    cfg, legacy = parse_config(src)

    # ---- configuration words
    if not cfg:
        inc = src.first(r"#\s*include\s*[<\"]xc\.h[>\"]")
        line = (inc[1] + 1) if inc and inc[0] == mainf else 1
        from .board import render_pragmas
        from .config import Settings
        block = render_pragmas(Settings().profile(), ics)
        F.append(AuditFinding("CFG_MISSING", "error", "No configuration bits (#pragma config) in the project: the chip runs "
                              "with erased defaults (FRCDIV 4 MHz, watchdog ON, JTAG ON, ICS PGx1).", mainf, line, CW_PAGES,
                              Fix(mainf, line, block.rstrip("\n"), insert=True)))
    else:
        if legacy:
            F.append(AuditFinding("CFG_LEGACY", "info", "Legacy _CONFIGx() macros: still accepted by XC16 but deprecated; "
                                  "prefer #pragma config (chipllama pragmas prints a full block).", *_loc(cfg, "FWDTEN", mainf),
                                  CW_PAGES))
        wdt = cfg.get("FWDTEN")
        clr = src.any(r"\bClrWdt\s*\(|__builtin_clrwdt|asm\s*\(\s*\"\s*clrwdt", re.I)
        if (wdt is None or wdt[0] == "ON") and not clr:
            f, ln = (wdt[1], wdt[2]) if wdt else _cfg_anchor(cfg, mainf)
            fx = None
            if wdt and not legacy:
                fx = Fix(wdt[1], wdt[2], re.sub(r"FWDTEN\s*=\s*ON", "FWDTEN = OFF", wdt[3]))
            elif not wdt and not legacy:
                fx = Fix(f, ln + 1, "#pragma config FWDTEN = OFF     // Watchdog timer disabled", insert=True)
            F.append(AuditFinding("WDT_RESET", "error", "Watchdog enabled (FWDTEN=" + (wdt[0] if wdt else "ON by default")
                                  + ") but the code never calls ClrWdt(): the chip resets every WDT period (~2 s at "
                                    "PS32768/PR128).", f, ln, [288, 131], fx))
        jt = cfg.get("JTAGEN")
        used = sorted(p for p in JTAG_PINS if _pin_used(src, p))
        if (jt is None or jt[0] == "ON") and used:
            f, ln = (jt[1], jt[2]) if jt else _cfg_anchor(cfg, mainf)
            fx = Fix(jt[1], jt[2], re.sub(r"JTAGEN\s*=\s*ON", "JTAGEN = OFF", jt[3])) if jt and not legacy else (
                Fix(f, ln + 1, "#pragma config JTAGEN = OFF     // JTAG disabled (frees RA0/RA1/RA4/RA5)", insert=True)
                if not legacy else None)
            F.append(AuditFinding("JTAG_PINS", "error", "JTAG port is enabled (JTAGEN=" + (jt[0] if jt else "ON by default")
                                  + ") but the code uses " + ", ".join(f"{p} ({JTAG_PINS[p]})" for p in used)
                                  + ": these pins stay owned by JTAG.", f, ln, [288, 133], fx))
        ic = cfg.get("ICS")
        if ic and ic[0] != ics and ic[0] in ICS_PAIRS:
            F.append(AuditFinding("ICS_MISMATCH", "warning", f"ICS = {ic[0]} but your board/debugger uses {ics}: "
                                  "debugging (not programming) will fail to connect.", ic[1], ic[2], [288],
                                  None if legacy else Fix(ic[1], ic[2], re.sub(r"ICS\s*=\s*\w+", f"ICS = {ics}", ic[3]))))
        if "FNOSC" not in cfg:
            F.append(AuditFinding("CLK_DEFAULT", "warning", "FNOSC not configured: the chip starts on FRCDIV (8 MHz/2 = "
                                  "4 MHz Fosc, Fcy 2 MHz).", *_cfg_anchor(cfg, mainf), [290, 121]))

    # ---- clock vs FCY
    fcy, why = fcy_from_config(cfg, regs, crystal_hz)
    rep.fcy = fcy
    delays = src.first(r"\b__delay_(?:ms|us)\s*\(")
    fdef = src.first(r"#\s*define\s+FCY\s+\(?\s*([0-9.eE]+)\s*(?:[uUlL]*)\s*\)?")
    if delays and not src.any(r"#\s*define\s+FCY\b"):
        inc = src.first(r"#\s*include\s*<libpic30\.h>")
        f, ln = (inc[0], inc[1]) if inc else (delays[0], 1)
        F.append(AuditFinding("FCY_MISSING", "error", "__delay_ms/__delay_us used but FCY is not defined (libpic30 needs it "
                              "before #include <libpic30.h>).", delays[0], delays[1], [121],
                              Fix(f, ln, f"#define FCY {int(fcy or 16e6)}UL", insert=True)))
    elif fdef and fcy:
        val = float(fdef[2].group(1))
        if abs(val - fcy) / fcy > 0.02:
            F.append(AuditFinding("FCY_WRONG", "error", f"#define FCY {val:g} but the configured clock gives Fcy = "
                                  f"{fcy/1e6:g} MHz ({why}): every __delay and baud/timer value is off by "
                                  f"x{val/fcy:.3g}.", fdef[0], fdef[1], [121, 125],
                                  Fix(fdef[0], fdef[1], re.sub(r"FCY\s+\(?\s*[0-9.eE]+\s*[uUlL]*\s*\)?", f"FCY {int(fcy)}UL", fdef[3]))))

    # ---- PPS for enabled peripherals
    outs = {}
    for rp in range(OUTPUT_RP_MAX + 1):
        reg, fld = rpor_field(rp)
        if reg in regs:
            v = emu.get(reg, fld)
            if v:
                outs.setdefault(v, []).append(rp)
    for n, fn in UART_TX_FN.items():
        if f"U{n}MODE" in chipdb.REGISTERS and emu.get(f"U{n}MODE", "UARTEN"):
            hit = src.first(rf"\bU{n}MODE(?:bits\.UARTEN)?\s*[|]?=")
            f, ln = (hit[0], hit[1]) if hit else (mainf, 1)
            if fn not in outs and emu.get(f"U{n}STA", "UTXEN"):
                F.append(AuditFinding("PPS_UART_TX", "error", f"UART{n} transmitter is enabled but U{n}TX is not mapped to any "
                                      f"RP pin (RPORx.RPnR = {fn}): nothing leaves the chip.", f, ln, [136, 138, 154],
                                      hint=f"__builtin_write_OSCCONL(OSCCON & 0xBF); RPOR5bits.RP10R = {fn}; "
                                           "__builtin_write_OSCCONL(OSCCON | 0x40);"))
            rxr, rxf = UART_RX[n]
            if emu.get(rxr, rxf) == 0x3F and src.any(rf"\bU{n}RXREG\b"):
                F.append(AuditFinding("PPS_UART_RX", "error", f"U{n}RXREG is read but U{n}RX is not mapped "
                                      f"({rxr}bits.{rxf} still 0x3F): no byte can ever arrive.", f, ln, [136, 147]))
    for n, fns in SPI_OUT_FN.items():
        if f"SPI{n}STAT" in chipdb.REGISTERS and emu.get(f"SPI{n}STAT", "SPIEN") and emu.get(f"SPI{n}CON1", "MSTEN"):
            missing = [k for k in ("SCK", "SDO") if fns[k] not in outs]
            if missing:
                hit = src.first(rf"\bSPI{n}STAT")
                F.append(AuditFinding("PPS_SPI", "error", f"SPI{n} master enabled but " + " and ".join(f"{k}{n}OUT" if k == "SCK"
                                      else f"{k}{n}" for k in missing) + " not mapped to an RP pin.",
                                      hit[0] if hit else mainf, hit[1] if hit else 1, [136, 138]))

    # ---- analog / digital
    pcfg = regs.get("AD1PCFGL", 0)
    for b in range(16):
        if pcfg >> b & 1:
            continue
        hit = src.first(rf"\bPORTBbits\.RB{b}\b|\b_RB{b}\b(?!\s*=)")
        if hit:
            ind = _indent(hit[3])
            fx = _pcfg_fix(src, b)
            F.append(AuditFinding("ANALOG_PIN", "error", f"RB{b} is read as a digital input but AN{b} is still analog "
                                  f"(AD1PCFGL bit {b} = 0 after reset): PORTB reads 0.", hit[0], hit[1], [273, 133], fx,
                                  hint=f"{ind}AD1PCFGLbits.PCFG{b} = 1;"))

    # ---- TRIS / LAT / PORT writes
    for name, ln, m, text in src.find(r"\bPORT([A-G])bits\.R\1(\d{1,2})\s*(\^=|\|=|&=|=)(?!=)"):
        port, bit = m.group(1), m.group(2)
        F.append(AuditFinding("RMW_PORT", "warning", f"Writing PORT{port}bits.R{port}{bit}: read-modify-write on PORT can "
                              "corrupt other pins of the port; write LAT instead.", name, ln, [133],
                              Fix(name, ln, text.replace(m.group(0), f"LAT{port}bits.LAT{port}{bit} {m.group(3)}"))))
    done = set()
    for name, ln, m, text in src.find(r"\b(?:LAT([A-G])bits\.LAT\1(\d{1,2})|_LAT([A-G])(\d{1,2}))\s*(\^=|\|=|&=|=)(?!=)"):
        port, bit = m.group(1) or m.group(3), int(m.group(2) or m.group(4))
        if (port, bit) in done or f"TRIS{port}" not in regs:
            continue
        done.add((port, bit))
        if regs[f"TRIS{port}"] >> bit & 1:
            F.append(AuditFinding("TRIS_INPUT", "error", f"LAT{port}{bit} is written but TRIS{port}{bit} is never cleared: "
                                  "the pin stays an input and never drives.", name, ln, [133],
                                  Fix(name, ln, f"{_indent(text)}TRIS{port}bits.TRIS{port}{bit} = 0;   /* output */", insert=True)))

    # ---- interrupts
    funcs = {}
    for name, txt in src.clean.items():
        if not name.endswith(".c"):
            continue
        for fn in split_functions(txt):
            funcs[fn.name] = (name, fn)
    defined, misnamed = set(), set()
    for fname, (file, fn) in funcs.items():
        is_isr = fn.is_isr or re.search(r"__attribute__\s*\(\(\s*(?:__)?interrupt", fn.header or "")
        if not is_isr:
            continue
        hdr_line = _line_of(src.clean[file], fn.line, fname)
        base = fname.lstrip("_")
        base = base[3:] if base.startswith("Alt") else base
        if base not in chipdb.VECTORS:
            sug = irq.suggest(fname)
            if sug:
                misnamed.add(sug.lstrip("_"))
            F.append(AuditFinding("ISR_NAME", "error", f"{fname} is not a valid interrupt vector name: the linker places "
                                  f"it nowhere and the interrupt goes to the default handler (reset)."
                                  + (f" Did you mean {sug}?" if sug else ""), file, hdr_line, [84],
                                  Fix(file, hdr_line, src.files[file].split("\n")[hdr_line - 1].replace(fname, sug)) if sug else None))
            continue
        defined.add(base)
        v = irq.vector(base)
        if not isr_clears_flag(emu, fn, v.ifs):
            end = _close_line(src.clean[file], hdr_line)
            F.append(AuditFinding("ISR_FLAG", "error", f"{fname} never clears {v.ifs[0]}bits.{v.ifs[1]}: the ISR re-enters "
                                  "forever and main() stops running.", file, hdr_line, v.pages,
                                  Fix(file, end, f"    {v.ifs[0]}bits.{v.ifs[1]} = 0;", insert=True) if end else None))
        if not re.search(r"auto_psv|no_auto_psv", fn.header or ""):
            line = src.files[file].split("\n")[hdr_line - 1]
            new = re.sub(r"__attribute__\s*\(\(\s*(__)?interrupt(__)?\s*", lambda m: m.group(0).rstrip() + ", no_auto_psv", line, count=1)
            F.append(AuditFinding("ISR_PSV", "warning", f"{fname} has no auto_psv/no_auto_psv attribute: XC16 warns and "
                                  "assumes auto_psv (slower entry).", file, hdr_line, [84],
                                  Fix(file, hdr_line, new) if new != line else None))
    for vname, n in chipdb.VECTORS.items():
        try:
            v = irq.vector(vname)
        except Exception:
            continue
        if vname not in defined and vname not in misnamed and emu.get(*v.iec) and v.iec[0] in regs:
            hit = src.first(rf"\b{v.iec[0]}bits\.{v.iec[1]}\s*=|\b_{v.iec[1]}\s*=|\b{v.iec[0]}\s*[|]?=")
            mh = src.first(r"^\s*int\s+main\s*\(")
            stub = (f"void __attribute__((interrupt, no_auto_psv)) _{vname}(void)\n{{\n    /* TODO: handle {vname} */\n"
                    f"    {v.ifs[0]}bits.{v.ifs[1]} = 0;\n}}\n")
            F.append(AuditFinding("IRQ_NO_ISR", "error", f"{v.iec[0]}.{v.iec[1]} enables {vname} but no _{vname}() is "
                                  "defined: the first interrupt jumps to the default handler and resets the chip.",
                                  hit[0] if hit else mainf, hit[1] if hit else 1, v.pages,
                                  Fix(mh[0], mh[1], stub, insert=True) if mh else None))

    # ---- volatile shared variables
    globals_ = {}
    for name, ln, m, text in src.find(r"^\s*((?:static\s+|volatile\s+|unsigned\s+|signed\s+|const\s+)*(?:int|char|long|short|"
                                      r"uint\d+_t|int\d+_t|bool|float)\b(?:\s+(?:volatile|unsigned|int|long))*)\s+([A-Za-z_]\w*)\s*(=[^;]*)?;"):
        if _depth_at(src.clean[name], ln) == 0:
            globals_[m.group(2)] = (name, ln, m, text)
    for var, (file, ln, m, text) in globals_.items():
        if "volatile" in m.group(1):
            continue
        in_isr = any((fn.is_isr or "interrupt" in (fn.header or "")) and re.search(rf"\b{var}\b\s*(?:[-+*/|&^]?=|\+\+|--)|(?:\+\+|--)\s*\b{var}\b", fn.body)
                     for _, fn in funcs.values())
        in_main = any(not (fn.is_isr or "interrupt" in (fn.header or "")) and re.search(rf"\b{var}\b", fn.body)
                      for _, fn in funcs.values())
        if in_isr and in_main:
            F.append(AuditFinding("VOLATILE", "warning", f"'{var}' is changed in an ISR and read in normal code but is not "
                                  "volatile: the optimiser may keep a stale copy (loops that never exit).", file, ln, [],
                                  Fix(file, ln, text.replace(m.group(1), "volatile " + m.group(1).strip(), 1))))

    F.sort(key=lambda f: ({"error": 0, "warning": 1, "info": 2}[f.severity], f.file, f.line))
    return rep


def _loc(cfg, key, mainf):
    v = cfg.get(key) or next(iter(cfg.values()), None)
    return (v[1], v[2]) if v else (mainf, 1)


def _cfg_anchor(cfg, mainf):
    last = max((v for v in cfg.values()), key=lambda v: v[2], default=None)
    return (last[1], last[2]) if last else (mainf, 1)


def _pin_used(src: Src, pin: str) -> bool:
    port, bit = pin[1], pin[2:]
    return src.any(rf"\b(?:TRIS{port}bits\.TRIS{port}{bit}|LAT{port}bits\.LAT{port}{bit}|PORT{port}bits\.R{port}{bit}|"
                   rf"_(?:TRIS|LAT|R){port}{bit})\b")


def _line_of(text: str, start: int, name: str) -> int:
    lines = text.split("\n")
    for i in range(max(0, start - 1), min(len(lines), start + 8)):
        if re.search(rf"\b{name}\s*\(", lines[i]):
            return i + 1
    for i, l in enumerate(lines):
        if re.search(rf"\b{name}\s*\(", l) and not l.rstrip().endswith(";"):
            return i + 1
    return start


def _close_line(text: str, start: int) -> int | None:
    """Line number of the brace closing the function that starts at `start`."""
    lines, depth, opened = text.split("\n"), 0, False
    for i in range(start - 1, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth, opened = depth + 1, True
            elif ch == "}":
                depth -= 1
                if opened and depth == 0:
                    return i + 1
    return None


def _pcfg_fix(src: Src, b: int) -> Fix | None:
    """Make AN<b> digital: flip an explicit 'PCFG<b> = 0', else add a line after the last AD1PCFGL write, else after TRIS."""
    hits = list(src.find(r"\bAD1PCFGL(?:bits\.PCFG(\d+))?\s*(?:[|&]?=)(?!=)"))
    for name, ln, m, text in reversed(hits):
        if m.group(1) == str(b) and re.search(r"=\s*0\s*;", text):
            return Fix(name, ln, re.sub(r"=\s*0\s*;", "= 1;   /* digital (ChipLlama audit) */", text, count=1))
    if hits:
        name, ln, m, text = hits[-1]
        return Fix(name, ln + 1, f"{_indent(text)}AD1PCFGLbits.PCFG{b} = 1;   /* RB{b}/AN{b} digital */", insert=True)
    tr = src.first(rf"\bTRISBbits\.TRISB{b}\s*=")
    if tr:
        return Fix(tr[0], tr[1] + 1, f"{_indent(tr[3])}AD1PCFGLbits.PCFG{b} = 1;   /* RB{b}/AN{b} digital */", insert=True)
    return None


def _depth_at(text: str, line: int) -> int:
    pre = "\n".join(text.split("\n")[:line - 1])
    return pre.count("{") - pre.count("}")


def audit_path(path, **kw) -> AuditReport:
    files, kind, device = load_path(path)
    return audit_files(files, device=device, project=kind, **kw)


def fixes_zip(rep: AuditReport) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, txt in rep.patched().items():
            z.writestr(name, txt)
        z.writestr("CHIPLLAMA_AUDIT.md", report_markdown(rep))
        if rep.diff():
            z.writestr("chipllama_fixes.diff", rep.diff())
    return buf.getvalue()


def report_markdown(rep: AuditReport) -> str:
    c = rep.counts()
    out = [f"# ChipLlama audit — score {rep.score}/100", "",
           f"Device {rep.device} · {rep.project} · {len(rep.files)} files · {c['error']} errors, {c['warning']} warnings, {c['info']} info"
           + (f" · Fcy {rep.fcy/1e6:g} MHz" if rep.fcy else ""), ""]
    for f in rep.findings:
        pg = (" (DS39897C p." + ", ".join(map(str, f.pages)) + ")") if f.pages else ""
        out.append(f"- **{f.severity.upper()} {f.rule}** `{f.file}:{f.line}` {f.message}{pg}" + (" — auto-fix available" if f.fix else ""))
    if rep.explanation:
        out += ["", "## Muse Spark review", "", rep.explanation]
    return "\n".join(out) + "\n"


def explain_prompt(rep: AuditReport) -> str:
    rows = "\n".join(f"- [{f.severity}] {f.rule} {f.file}:{f.line} {f.message} pages={f.pages}" for f in rep.findings)
    code = "\n\n".join(f"// ===== {k}\n{v[:5000]}" for k, v in list(rep.files.items())[:4])
    return ("You are reviewing embedded C for the PIC24FJ256GB110 (datasheet DS39897C). Deterministic rules already found:\n"
            f"{rows or '(none)'}\n\nWrite a short audit report for the developer: for each finding, why it breaks the "
            "hardware and the exact fix, citing datasheet pages. Then list up to 3 extra risks you can see in the code that "
            "the rules did not cover, marked 'Muse (unverified)'. Do not invent register names. Be concise.\n\n" + code)


def transcribe_prompt() -> str:
    return ("Transcribe the C source code visible in these screenshots EXACTLY, character for character, as one file. "
            "Output only the code, no markdown fences, no commentary. If a part is unreadable write /* ?? */.")


# ------------------------------------------------------------------ optional extras (model / compiler)
def compile_files(files: dict[str, str], settings) -> dict:
    """Compile every .c file (patched or not) on its own, with all headers next to it. Never links or flashes."""
    import tempfile

    from .compiler import compile_c
    results, status = [], "ok"
    for name, text in files.items():
        if not name.lower().endswith(".c"):
            continue
        tmp = tempfile.mkdtemp(prefix="chipllama_audit_")
        for h, ht in files.items():
            if h.lower().endswith(".h"):
                (Path(tmp) / Path(h).name).write_text(ht, encoding="utf-8")
        r = compile_c(text, settings, workdir=tmp)
        d = r.to_dict()
        d["file"] = name
        results.append(d)
        if r.status == "skipped":
            status = "skipped"
            break
        if r.status == "error":
            status = "error"
    return {"status": status, "files": results}


def enrich(rep: AuditReport, model=None, settings=None, explain: bool = False, compile: bool = False,
           patched: bool = True) -> AuditReport:
    if explain and model is not None:
        from .models.base import Message
        try:
            rep.explanation = model.complete([Message("user", explain_prompt(rep))]).strip()
        except Exception as e:  # model errors must not lose the deterministic report
            rep.notes.append(f"Muse explanation failed: {e}")
    elif explain:
        rep.notes.append("Muse explanation skipped: no model configured (MODEL_API_KEY in .env).")
    if compile and settings is not None:
        rep.compile = compile_files(rep.patched() if patched else rep.files, settings)
    return rep


def transcribe(images: list[bytes], model) -> str:
    from .models.base import Message
    txt = model.complete([Message("user", transcribe_prompt(), images)]).strip()
    txt = re.sub(r"^```[a-zA-Z]*\n|\n?```$", "", txt)
    return txt
