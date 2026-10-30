"""Generate C for every Blocks example (examples/blocks/*.json) and compile it with xc16-gcc.

python scripts/compile_blocks_examples.py [--link] [--require-compiler]
Prints one line per example: VERIFIED (checker clean + compiled), FAILED (generation, checker or
compiler error) or SKIPPED (xc16-gcc not found; checker still ran). Exit code 1 if any FAILED
(or, with --require-compiler, if the compiler is missing).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chipllama import blocks, compiler  # noqa: E402
from chipllama.config import Settings  # noqa: E402


def settings() -> Settings:
    for name in ("from_env", "load"):
        f = getattr(Settings, name, None)
        if callable(f):
            return f()
    return Settings()


def run(link: bool = False, require: bool = False, out=print, verbose: bool = False) -> int:
    s = settings()
    comp, path = compiler.find_compiler(s)
    out(f"device {s.device}; compiler {comp}: {path or 'NOT FOUND'}")
    failed = 0
    files = sorted((ROOT / "examples" / "blocks").glob("*.json"))
    for f in files:
        r = blocks.generate(json.loads(f.read_text(encoding="utf-8")), s)
        if r["error"] or not r["c"]:
            failed += 1
            out(f"FAILED   {f.stem}: generation: {r['error']}")
            continue
        errs = [x for x in r["findings"] if x.get("severity") == "error"]
        if errs:
            failed += 1
            out(f"FAILED   {f.stem}: checker: {errs[0].get('code')} {errs[0].get('message', '')}")
            continue
        c = compiler.compile_c(r["c"], s, link=link)
        warns = [d for d in c.diagnostics if d.severity == "warning"]
        wtxt = f", {len(warns)} warning(s): " + "; ".join(f"line {d.line}: {d.message}" for d in warns[:3]) if warns else ""
        if c.status == "ok" and (not link or c.hex_path):
            out(f"VERIFIED {f.stem} ({len(r['c'].splitlines())} lines" + (", .hex built" if link else "") + f"){wtxt}")
        elif c.status == "skipped":
            out(f"SKIPPED  {f.stem}: checker clean, {c.reason}")
            failed += require
        else:
            failed += 1
            errs = [d for d in c.diagnostics if d.severity != "warning"]
            if errs:
                why = f"line {errs[0].line}: {errs[0].message}"
            else:
                bad = [l.strip() for l in c.output.splitlines() if "undefined reference" in l or "error" in l.lower()]
                why = (bad[0] if bad else c.output.strip()[-300:]) or "no .hex produced"
            out(f"FAILED   {f.stem}: {comp}: {why}{wtxt}")
            if verbose:
                out("    cmd: " + " ".join(c.command or []))
    out(f"{len(files)} examples, {failed} failed")
    failed += run_circuits(s, require, out, verbose)
    return 1 if failed else 0


def run_circuits(s, require: bool = False, out=print, verbose: bool = False) -> int:
    """Build every Circuit example (examples/circuit/*.json) as a full MPLAB project and compile it to .hex."""
    from chipllama import projgen
    failed = 0
    files = sorted((ROOT / "examples" / "circuit").glob("*.json"))
    for f in files:
        res = projgen.build(json.loads(f.read_text(encoding="utf-8")))
        if not res["erc_ok"] or not res["verified"]:
            failed += 1
            out(f"FAILED   circuit/{f.stem}: ERC ok={res['erc_ok']} verified={res['verified']}")
            continue
        c = compiler.compile_project(res, s)
        if c.status == "ok" and c.hex_path:
            out(f"VERIFIED circuit/{f.stem} ({res['device']}, {len(res['files'])} files, .hex built: {c.hex_path})")
        elif c.status == "skipped":
            out(f"SKIPPED  circuit/{f.stem} ({res['device']}): verified, {c.reason}")
            failed += require
        else:
            failed += 1
            bad = [l.strip() for l in (c.output or "").splitlines() if "error" in l.lower()]
            out(f"FAILED   circuit/{f.stem}: {(bad[0] if bad else c.reason or 'no .hex')}")
            if verbose:
                out("    cmd: " + " ".join(c.command or []))
    out(f"{len(files)} circuit projects, {failed} failed")
    return failed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--link", action="store_true", help="link to .elf/.hex instead of -c")
    ap.add_argument("--require-compiler", action="store_true", help="treat a missing compiler as failure")
    ap.add_argument("-v", "--verbose", action="store_true", help="print the compiler command on failure")
    a = ap.parse_args()
    sys.exit(run(a.link, a.require_compiler, verbose=a.verbose))
