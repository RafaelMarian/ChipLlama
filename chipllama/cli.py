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
    from .doctor import doctor
    for r in doctor(s):
        print(f"{r['label']:<18}: {r['value']}")
    return 0


def cmd_devices(a, s: Settings) -> int:
    from . import edc
    roots = edc.pack_roots(s.mplab_packs_dir)
    devs = edc.search_devices(a.query or "", s.mplab_packs_dir, a.limit)
    if not roots:
        print("no MPLAB X packs folder found; set MPLAB_PACKS_DIR (e.g. C:\\Program Files\\Microchip\\MPLABX\\v6.25\\packs)")
    for d in devs:
        print(f"{d['name']:<22} {d['family']:<8} {d['compiler']:<5} {d['path']}")
    print(f"{len(devs)} device(s) in: " + ("; ".join(roots) or "-"))
    return 0 if devs else 1


def cmd_pragmas(a, s: Settings) -> int:
    from . import chipdb, pragmas
    from .board import render_pragmas
    dev = chipdb.get_device(a.device, s.mplab_packs_dir, s.cache_dir, a.edc)
    if dev is None:
        print(f"no EDC file for {a.device} (set MPLAB_PACKS_DIR or pass --edc path/to/{a.device.upper()}.PIC)", file=sys.stderr)
        return 2
    ics = a.ics or s.ics
    if chipdb.is_builtin_family(dev.name) and not a.generic:
        print(f"// {dev.name}: board profile '{s.clock_profile}' (verified clock tree), data: {dev.source}")
        print(render_pragmas(s.profile(), ics))
    else:
        print(pragmas.render(dev, ics))
    return 0


def cmd_calc_uart(a, s) -> int:
    from . import uart as u
    fcy = (a.fosc * 1e6 if a.fosc else s.profile().fosc_hz) / 2
    baud = a.baud or 9600
    print(f"UART1 {baud} baud @ Fcy={fcy/1e6:g} MHz  (U1BRG = Fcy/(16|4 * baud) - 1, DS39897C p.200)")
    best = u.solve_baud(fcy, baud)
    for o in u.baud_options(fcy, baud):
        print(f"  BRGH={o.brgh}: U1BRG={o.brg:<5d} -> {o.actual:.0f} baud ({o.error_pct:+.2f}%)" + ("  <- best" if o == best else ""))
    for role, tok in (("TX", a.tx), ("RX", a.rx)):
        if not tok:
            continue
        rp = u._pin_to_rp(tok)
        if rp is None or rp not in u.RP_PIN:
            print(f"  {role}: unknown pin '{tok}'")
            continue
        pin = u.port_pin(rp)
        an = u.analog_channel(pin)
        if role == "TX":
            if rp > u.OUTPUT_RP_MAX:
                print(f"  TX: {u.rp_name(rp)} is input-only (cannot carry U1TX)")
                continue
            reg, f = u.rpor_field(rp)
            line = f"  TX RP{rp}={pin}: {reg}bits.{f} = 3; TRIS{pin[1]}bits.TRIS{pin[1]}{pin[2:]} = 0"
        else:
            line = f"  RX {u.rp_name(rp)}={pin}: RPINR18bits.U1RXR = {rp}; TRIS{pin[1]}bits.TRIS{pin[1]}{pin[2:]} = 1"
        print(line + (f"; AD1PCFGLbits.PCFG{an} = 1" if an is not None else ""))
    if a.tx or a.rx:
        print("  PPS: __builtin_write_OSCCONL(OSCCON & 0xBF); ...map...; __builtin_write_OSCCONL(OSCCON | 0x40);")
    if a.json:
        print(json.dumps({"fcy": fcy, "baud": baud, "best": best.to_dict(),
                          "options": [o.to_dict() for o in u.baud_options(fcy, baud)]}, indent=2))
    return 0


def cmd_calc(a, s) -> int:
    if a.request.strip().lower() == "uart":
        return cmd_calc_uart(a, s)
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
        if sp:
            print(f"[spec] Timer{sp['timer']} period={sp['period_s']*1e3:g} ms Fosc={sp['fosc_hz']/1e6:g} MHz irq={sp['interrupt']}")
        if data.get("uart"):
            u = data["uart"]
            b = u["solution"]
            print(f"[spec] UART{u['uart']} {u['baud']} baud TX={u['tx_rp'] if u['tx_rp'] is not None else '-'} "
                  f"RX={u['rx_rp'] if u['rx_rp'] is not None else '-'} -> BRGH={b['brgh']} U1BRG={b['brg']} ({b['error_pct']:+.2f}%)")
        if data.get("demo_fault_injection"):
            print("[demo] fault injection ON: attempt 1 will be corrupted on purpose")
    elif ev == "fault":
        print(f"[DEMO FAULT INJECTION] attempt {data['n']}: {data['description']}")
        for line in (data.get("diff") or "").splitlines():
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                print("    " + line)
    elif ev == "reflect":
        pg = ", ".join(f"p.{p}" for p in data["pages"]) or "-"
        print(f"[reflect] feeding back {', '.join(data['errors']) or 'errors'}; datasheet {pg}")
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
                    on_event=_print_attempt, inject_fault=a.inject_fault)
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
    tag = " (demo fault injected on attempt 1, fixed by the agent)" if res.fault and res.fault.get("kind") != "none" and res.status == "verified" else ""
    print(f"{'VERIFIED' if res.status == 'verified' else 'FAILED'} after {len(res.attempts)} attempt(s){tag}")
    return 0 if res.status == "verified" else 1


def cmd_debug(a, s: Settings) -> int:
    if a.image and (a.intent is not None or a.extraction):
        return cmd_debug_vision(a, s)
    text = Path(a.dump).read_text(encoding="utf-8") if a.dump else (sys.stdin.read() if not sys.stdin.isatty() and not a.image else "")
    imgs = [Path(p).read_bytes() for p in (a.image or [])]
    model = None
    if not a.no_model:
        try:
            model = make_model(s, a.backend)
        except ValueError as e:
            print(e, file=sys.stderr)
    r = debug_session(text, imgs, a.symptom or "", Path(a.code).read_text(encoding="utf-8") if a.code else "", model,
                      (a.fosc or 32.0) * 1e6, a.expected_ms / 1e3 if a.expected_ms else None)
    print(json.dumps(r.to_dict(), indent=2) if a.json else _fmt_debug(r))
    return 0


def cmd_debug_vision(a, s: Settings) -> int:
    from .vision_debug import COSC_FOR_PROFILE, format_report, normalize, vision_debug
    imgs = [Path(p).read_bytes() for p in a.image]
    code = Path(a.code).read_text(encoding="utf-8") if a.code else ""
    ex = normalize(json.loads(Path(a.extraction).read_text(encoding="utf-8"))) if a.extraction else None
    model = None
    if not a.no_model:
        try:
            model = make_model(s, a.backend)
        except ValueError as e:
            print(e, file=sys.stderr)
    if ex is None and model is None:
        print("reading an image needs the Muse model (MODEL_API_KEY in .env), or pass --extraction file.json", file=sys.stderr)
        return 2
    fosc = a.fosc * 1e6 if a.fosc else s.profile().fosc_hz
    try:
        rep = vision_debug(imgs, a.intent or "", code, model, fosc, COSC_FOR_PROFILE.get(s.clock_profile, 1), s.crystal_hz,
                           extraction=ex, device=a.device or s.device, clock_explicit=bool(a.fosc),
                           packs_dir=s.mplab_packs_dir, cache_dir=s.cache_dir)
    except ModelError as e:
        print(f"vision debug failed: {e}", file=sys.stderr)
        return 2
    print(json.dumps(rep.to_dict(), indent=2, default=str) if a.json else format_report(rep))
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
    url = f"http://{a.host}:{a.port}"
    print(f"ChipLlama dashboard on {url}  (Ctrl+C to stop)")
    if not getattr(a, "no_browser", False):
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("chipllama.server:get_app", host=a.host, port=a.port, factory=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("chipllama", description="Verifiable AI copilot for Microchip PIC MCUs (XC8/XC16/XC32)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="show configuration / toolchain status").set_defaults(fn=cmd_doctor)
    dv = sub.add_parser("devices", help="list PIC devices found in the MPLAB X packs (EDC files)")
    dv.add_argument("query", nargs="?", default=""); dv.add_argument("--limit", type=int, default=500)
    dv.set_defaults(fn=cmd_devices)
    pr = sub.add_parser("pragmas", help="print a complete safe #pragma config block for a device")
    pr.add_argument("device"); pr.add_argument("--ics", help="programmer pin pair, e.g. PGx2")
    pr.add_argument("--edc", help="explicit path to a <DEVICE>.PIC file")
    pr.add_argument("--generic", action="store_true", help="EDC safe defaults even for the built-in board profile")
    pr.set_defaults(fn=cmd_pragmas)
    c = sub.add_parser("calc", help="deterministic timer / UART calculator (no AI, no key). `calc uart --baud 115200`")
    c.add_argument("request", help="timer request text, or the word 'uart'")
    c.add_argument("--fosc", type=float, help="Fosc in MHz (overrides text / board profile)")
    c.add_argument("--baud", type=int, help="uart: baud rate"); c.add_argument("--tx", help="uart: TX pin, e.g. RP10 or RF4")
    c.add_argument("--rx", help="uart: RX pin, e.g. RP17 or RPI37")
    c.add_argument("--json", action="store_true", help="also print JSON")
    c.set_defaults(fn=cmd_calc)
    g = sub.add_parser("generate", help="request -> verified C code")
    g.add_argument("request"); g.add_argument("--backend", choices=["meta", "baseten"])
    g.add_argument("--max-iters", type=int, default=4); g.add_argument("--no-images", action="store_true")
    g.add_argument("--hint", action="store_true", help="give the calculator result to the model up front")
    g.add_argument("--out", help="write main.c here"); g.add_argument("--export", help="write MPLAB X-ready folder")
    g.add_argument("--flash", action="store_true", help="build + program via ipecmd (needs CHIPLLAMA_ENABLE_FLASH=1 and --yes)")
    g.add_argument("--yes", action="store_true", help="confirm flashing")
    g.add_argument("--inject-fault", action="store_true",
                   help="DEMO: deliberately corrupt the first attempt to show self-correction (clearly labelled)")
    g.set_defaults(fn=cmd_generate)
    d = sub.add_parser("debug", help="reverse-debug a register dump / screenshot")
    d.add_argument("--dump", help="text file with register values (or pipe on stdin)")
    d.add_argument("--image", action="append", help="photo/screenshot: MPLAB SFR/Watch window, register dump, scope or serial terminal")
    d.add_argument("--intent", help="what the firmware should do, e.g. 'Timer1 10 ms toggle RA0' or 'UART 115200' (enables the vision pipeline)")
    d.add_argument("--extraction", help="replay a saved vision extraction JSON instead of calling the model (offline demo)")
    d.add_argument("--device", help="PIC part for the vision pipeline (default: Settings device); uses the MPLAB EDC file when found")
    d.add_argument("--symptom"); d.add_argument("--code", help="C file under suspicion")
    d.add_argument("--fosc", type=float, default=None, help="assumed Fosc in MHz (default: board profile / 32)")
    d.add_argument("--expected-ms", type=float); d.add_argument("--backend", choices=["meta", "baseten"])
    d.add_argument("--no-model", action="store_true", help="deterministic analysis only"); d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_debug)
    pg = sub.add_parser("pages", help="render datasheet register pages to PNG")
    pg.add_argument("pages", nargs="*"); pg.add_argument("--out", default=".cache/pages_out"); pg.set_defaults(fn=cmd_pages)
    f = sub.add_parser("flash", help="OPTIONAL: program a .hex with ipecmd (PICkit 5 default)")
    f.add_argument("hex"); f.add_argument("--yes", action="store_true"); f.set_defaults(fn=cmd_flash)
    sv = sub.add_parser("serve", help="start the web UI")
    sv.add_argument("--host", default="127.0.0.1"); sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--no-browser", action="store_true"); sv.set_defaults(fn=cmd_serve)
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
