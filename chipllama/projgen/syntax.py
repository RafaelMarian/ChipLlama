"""Host-gcc syntax/type check of a generated project against a stand-in <xc.h> built from EDC SFR data.

This is not the Microchip compiler: it proves the C is well-formed, every register and bitfield the
project names exists on the device, and the HAL/driver/board prototypes agree. The real build is
`make` / MPLAB X (or compiler.compile_project) on a PC with XC8/XC16/XC32.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

WIDTH = {"xc8": "unsigned char", "xc16": "unsigned int", "xc32": "unsigned int"}


def fake_xc_h(dev, comp: str) -> str:
    t = "unsigned short" if comp == "xc16" else WIDTH[comp]      # host int is 32-bit: keep the 16-bit SFR width
    out = ["#ifndef FAKE_XC_H", "#define FAKE_XC_H", "#include <stdint.h>",
           "#define __interrupt(...)", "#define __delay_ms(x) ((void)(x))", "#define __delay_us(x) ((void)(x))",
           "#define __builtin_write_OSCCONL(x) ((void)(x))", "#define _CP0_GET_COUNT() (0U)", "#define Nop() ((void)0)",
           "#define __attribute__(x)", "static inline void __delay32(unsigned long n) { (void)n; }"]
    names = set()
    for name, r in sorted(dev.sfrs.items()):
        if not re.match(r"^[A-Za-z_]\w*$", name) or name in names:
            continue
        names.add(name)
        out.append(f"extern volatile {t} {name};")
        flds = [f for f in r.fields if re.match(r"^[A-Za-z_]\w*$", f.name)]
        if flds:
            seen, mem = set(), []
            for f in flds:
                if f.name in seen:
                    continue
                seen.add(f.name)
                pad = f"unsigned :{f.lsb}; " if f.lsb else ""
                mem.append(f"struct {{ {pad}unsigned {f.name}:{f.width}; }};")
            out.append(f"extern volatile union {{ {' '.join(mem)} }} {name}bits;")
    out.append("#endif")
    return "\n".join(out)


def syntax_check(res: dict, dev, gcc: str | None = None) -> dict:
    gcc = gcc or shutil.which("gcc")
    if not gcc or dev is None:
        return {"status": "skipped", "output": "no host gcc or no EDC data"}
    comp = res["compiler"]
    tmp = Path(tempfile.mkdtemp(prefix="chipllama_syn_"))
    (tmp / "fake").mkdir()
    (tmp / "fake" / "xc.h").write_text(fake_xc_h(dev, comp))
    (tmp / "fake" / "libpic30.h").write_text("/* stand-in */\n")
    for p, t in res["files"].items():
        f = tmp / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(t)
    srcs = sorted(p for p in res["files"] if p.endswith(".c"))
    inc = ["-I" + str(tmp / "fake")] + [f"-I{tmp / d}" for d in ("hal", "drivers", "board", "config")]
    outs, ok = [], True
    for s in srcs:
        cmd = [gcc, "-std=c99", "-fsyntax-only", "-Wall", "-Wextra", "-Wno-unused-parameter", "-Wno-unknown-pragmas",
               "-Werror=implicit-function-declaration", "-Werror=incompatible-pointer-types", *inc, str(tmp / s)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        text = (p.stdout + p.stderr).replace(str(tmp) + "/", "")
        if p.returncode != 0:
            ok = False
        if text.strip():
            outs.append(f"--- {s}\n{text}")
    shutil.rmtree(tmp, ignore_errors=True)
    return {"status": "ok" if ok else "error", "files": len(srcs), "output": "\n".join(outs)}
