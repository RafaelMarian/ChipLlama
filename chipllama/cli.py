"""Command line: python -m chipllama <command>  (or the `chipllama` script)."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .agent import run_agent
from .compiler import compile_c, find_tool
from .config import Settings
from .datasheet import load_page_images, pages_for_registers
from .debug import debug_session
from .export import export_project
from .flash import build_command, find_ipecmd, flash, part_name
from .models import ModelError, make_model
from .timers import parse_request, solve


def _utf8():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass


def cmd_doctor(a, s: Settings) -> int:
    print(f"device            : {s.device} (part for ipecmd: -P{part_name(s.device)})")
    print(f"backend           : {s.backend}")
    print(f"MODEL_API_KEY     : {'set' if s.meta_api_key else 'NOT set'}   model={s.meta_model}")
    print(f"BASETEN_API_KEY   : {'set' if s.baseten_api_key else 'NOT set'}   model={s.baseten_model}")
    print(f"xc16-gcc          : {find_tool('xc16-gcc', s.xc16_gcc) or 'NOT FOUND (compile step will be skipped)'}")
    print(f"datasheet PDF     : {s.datasheet_pdf} -> {'found' if Path(s.datasheet_pdf).is_file() else 'missing (text-only prompts)'}")
    print(f"flash enabled     : {s.enable_flash}   tool={s.ipecmd_tool}   ipecmd={find_ipecmd(s.ipecmd_path) or 'not found'}")
    return 0


def cmd_calc(a, s) -> int:
    spec = parse_request(a.request)
    if a.fosc:
        spec.fosc_hz = a.fosc * 1e6
    if spec.period_s is None:
        print("no period found in request", file=sys.stderr)
        return 2
    sol = solve(spec)
    print(f"Timer{spec.timer}: Fcy={spec.fcy/1e6:g} MHz  prescale 1:{sol.prescale} (TCKPS={sol.tckps})  "
          f"period reg={sol.period_reg}  achieved {sol.achieved_s*1e3:.6g} ms ({sol.error_ppm:.1f} ppm)")
    for name, val in sol.registers.items():
        print(f"  {name} = 0x{val:04X}  ({val})")
    if a.json:
        print(json.dumps(sol.to_dict(), indent=2, default=str))
    return 0


def _print_attempt(ev: str, data: dict) -> None:
    if ev == "spec":
        sp = data["spec"]
        print(f"[spec] Timer{sp['timer']} period={sp['period_s']*1e3:g} ms Fosc={sp['fosc_hz']/1e6:g} MHz irq={sp['interrupt']}")
    elif ev == "attempt" and "check" in data:
        errs = [f for f in data["check"]["findings"] if f["severity"] == "error"]
        print(f"[attempt {data['n']}] compile={data['compile']['status']} checker={'OK' if data['check']['ok'] else 'FAIL'} ({len(errs)} errors)")
        for f in errs:
            print(f"    - {f['code']}: {f['message']}")
        if data["compile"]["status"] == "skipped":
            print("    (compile skipped: " + data["compile"]["reason"][:70] + ")")


def cmd_generate(a, s: Settings) -> int:
    model = make_model(s, a.backend)
    res = run_agent(a.request, model, s, max_iters=a.max_iters, use_images=not a.no_images, hint=a.hint,
                    on_event=_print_attempt)
    print(f"\n=== {res.status.upper()} ({res.to_dict()['verification_level']}) backend={res.backend} ===")
    for n in res.notes:
        print("note:", n)
    if res.error:
        print("error:", res.error)
    if res.code:
        print(res.code)
    if a.out and res.code:
        Path(a.out).write_text(res.code, encoding="utf-8")
        print(f"wrote {a.out}")
    if a.export and res.code:
        paths = export_project(res.code, a.export, s.device, res.spec, res.solution)
        print("exported MPLAB X folder:", Path(a.export).resolve(), [p.name for p in paths])
    if a.flash:
        with tempfile.TemporaryDirectory() as td:
            cr = compile_c(res.code, s, link=True, workdir=td)
            if not cr.hex_path:
                print("flash: build failed or xc16 missing:", cr.reason or cr.feedback_text())
            else:
                fr = flash(cr.hex_path, s, confirmed=a.yes)
                print("flash:", fr.status, fr.reason, " ".join(fr.command))
    return 0 if res.status == "verified" else 1


def cmd_debug(a, s: Settings) -> int:
    text = Path(a.dump).read_text(encoding="utf-8") if a.dump else (sys.stdin.read() if not sys.stdin.isatty() and not a.image else "")
    imgs = [Path(p).read_bytes() for p in (a.image or [])]
    model = None
    if not a.no_model:
        try:
            model = make_model(s, a.backend)
        except ValueError as e:
            print(e, file=sys.stderr)
    r = debug_session(text, imgs, a.symptom or "", Path(a.code).read_text(encoding="utf-8") if a.code else "", model,
                      a.fosc * 1e6, a.expected_ms / 1e3 if a.expected_ms else None)
    print(json.dumps(r.to_dict(), indent=2) if a.json else _fmt_debug(r))
    return 0


def _fmt_debug(r) -> str:
    out = ["Registers: " + ", ".join(f"{k}=0x{v:04X}" for k, v in r.registers.items())]
    for d in r.decoded:
        if d["fields"]:
            out.append(f"  {d['register']:6s} {d['value']}  " + " ".join(f"{k}={v}" for k, v in d["fields"].items()))
    out.append("Findings:")
    out += [f"  [{f.severity}] {f.message}" for f in r.findings]
    if r.explanation:
        out += ["", "Explanation:", r.explanation]
    out += [f"note: {n}" for n in r.notes]
    return "\n".join(out)


def cmd_pages(a, s: Settings) -> int:
    pages = [int(p) for p in a.pages] if a.pages else pages_for_registers(["T1CON", "T2CON", "T3CON", "IFS0", "IEC0", "IPC0", "IPC1", "IPC2"])
    imgs, note = load_page_images(pages, s)
    print(note)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for p, b in imgs:
        (out / f"p{p:03d}.png").write_bytes(b)
    return 0 if imgs else 1


def cmd_flash(a, s: Settings) -> int:
    fr = flash(a.hex, s, confirmed=a.yes)
    print(fr.status, fr.reason)
    print(" ".join(fr.command))
    print(fr.output)
    return 0 if fr.status == "ok" else 1


def cmd_serve(a, s: Settings) -> int:
    import uvicorn
    print(f"ChipLlama UI on http://{a.host}:{a.port}")
    uvicorn.run("chipllama.server:get_app", host=a.host, port=a.port, factory=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("chipllama", description="Verifiable AI copilot for PIC24FJ256GB110 (XC16)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="show configuration / toolchain status").set_defaults(fn=cmd_doctor)
    c = sub.add_parser("calc", help="deterministic timer calculator (no AI, no key)")
    c.add_argument("request"); c.add_argument("--fosc", type=float, help="Fosc in MHz (overrides text)")
    c.add_argument("--json", action="store_true", help="also print JSON")
    c.set_defaults(fn=cmd_calc)
    g = sub.add_parser("generate", help="request -> verified C code")
    g.add_argument("request"); g.add_argument("--backend", choices=["meta", "baseten"])
    g.add_argument("--max-iters", type=int, default=4); g.add_argument("--no-images", action="store_true")
    g.add_argument("--hint", action="store_true", help="give the calculator result to the model up front")
    g.add_argument("--out", help="write main.c here"); g.add_argument("--export", help="write MPLAB X-ready folder")
    g.add_argument("--flash", action="store_true", help="build + program via ipecmd (needs CHIPLLAMA_ENABLE_FLASH=1 and --yes)")
    g.add_argument("--yes", action="store_true", help="confirm flashing")
    g.set_defaults(fn=cmd_generate)
    d = sub.add_parser("debug", help="reverse-debug a register dump / screenshot")
    d.add_argument("--dump", help="text file with register values (or pipe on stdin)")
    d.add_argument("--image", action="append", help="screenshot(s) of a debugger register view")
    d.add_argument("--symptom"); d.add_argument("--code", help="C file under suspicion")
    d.add_argument("--fosc", type=float, default=32.0, help="assumed Fosc in MHz")
    d.add_argument("--expected-ms", type=float); d.add_argument("--backend", choices=["meta", "baseten"])
    d.add_argument("--no-model", action="store_true", help="deterministic analysis only"); d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_debug)
    pg = sub.add_parser("pages", help="render datasheet register pages to PNG")
    pg.add_argument("pages", nargs="*"); pg.add_argument("--out", default=".cache/pages_out"); pg.set_defaults(fn=cmd_pages)
    f = sub.add_parser("flash", help="OPTIONAL: program a .hex with ipecmd (PICkit 5 default)")
    f.add_argument("hex"); f.add_argument("--yes", action="store_true"); f.set_defaults(fn=cmd_flash)
    sv = sub.add_parser("serve", help="start the web UI")
    sv.add_argument("--host", default="127.0.0.1"); sv.add_argument("--port", type=int, default=8000); sv.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    _utf8()
    args = build_parser().parse_args(argv)
    s = Settings.from_env()
    try:
        return args.fn(args, s)
    except ModelError as e:
        print(f"model error: {e}", file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
