"""FastAPI app + local single-page dashboard (no build step)."""
from __future__ import annotations

import dataclasses
import difflib
import io
import json
import queue
import re
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import __version__
from .agent import run_agent
from .board import ICS_PAIRS, PROFILE_KEYS, get_profile, render_pragmas
from .compiler import compile_c, find_tool, find_compiler
from . import chipdb, edc as edcmod, pragmas as pragmamod
from .config import Settings, apply_settings_file, public_view, save_settings, settings_path
from .datasheet import load_page_images
from .debug import debug_session
from .doctor import doctor
from .export import export_project
from .flash import find_ipecmd, flash, part_name
from .models import ModelError, make_model
from .timers import parse_request, solve

WEB = Path(__file__).parent / "web"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class GenerateReq(BaseModel):
    request: str
    max_iters: int | None = None
    use_images: bool = True
    hint: bool = False
    inject_fault: bool = False
    context: str = ""
    context_kind: str = "debug"


class BlocksReq(BaseModel):
    workspace: dict
    device: str | None = None      # M8: family board (with circuit)
    circuit: str | None = None     # M8: circuit example the blocks drive
    gen_mode: str | None = None


class CircuitReq(BaseModel):
    circuit: dict
    compile: bool = False
    part: str = ""
    fmt: str = "kicad"


class CalcReq(BaseModel):
    request: str


class ExportReq(BaseModel):
    code: str
    request: str = ""


class FlashReq(BaseModel):
    code: str
    confirm: bool = False


def _diff(a: str, b: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), "previous", "this attempt", n=2))


def create_app(settings: Settings | None = None, model_factory: Callable | None = None,
               compile_fn: Callable = compile_c, settings_file: str | Path | None = None) -> FastAPI:
    base = settings or Settings.from_env(settings_file)
    sfile = Path(settings_file) if settings_file else settings_path()
    state: dict[str, Any] = {"s": base}
    if settings is not None:            # explicit Settings: still honour the UI's settings.json if present
        st = dataclasses.replace(base)
        apply_settings_file(st, sfile)
        state["s"] = st

    def cur() -> Settings:
        return state["s"]

    mk = model_factory or (lambda: make_model(cur()))
    app = FastAPI(title="ChipLlama", version=__version__)

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    # ------------------------------------------------------------ status / settings
    @app.get("/vendor/{name}")
    def vendor(name: str):
        f = WEB / "vendor" / name
        if "/" in name or "\\" in name or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f)

    @app.get("/fonts/{name}")
    def fonts(name: str):
        f = WEB / "fonts" / name
        if "/" in name or "\\" in name or f.suffix != ".woff2" or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f, media_type="font/woff2", headers={"Cache-Control": "max-age=86400"})

    @app.get("/static/{name}")
    def static(name: str):
        f = WEB / name
        if "/" in name or "\\" in name or not f.is_file() or f.suffix not in (".js", ".css", ".woff2", ".svg", ".png", ".cur", ".mp3", ".wav", ".ogg"):
            raise HTTPException(404)
        return FileResponse(f)

    # ------------------------------------------------------------ blocks
    @app.get("/api/blocks/meta")
    def blocks_meta():
        from . import blocks as bl
        ex = []
        for f in sorted(EXAMPLES.glob("blocks/*.json")):
            ex.append({"name": f.stem, "title": json.loads(f.read_text(encoding="utf-8")).get("title", f.stem)})
        for f in sorted(EXAMPLES.glob("blocks/families/*.json")):
            j = json.loads(f.read_text(encoding="utf-8"))
            ex.append({"name": f.stem, "title": j.get("title", f.stem), "family": True,
                       "device": j.get("device"), "circuit": j.get("circuit")})
        return {"pins": bl.pin_meta(), "examples": ex}

    @app.get("/api/blocks/parts")
    def blocks_parts(circuit: str):
        from .families import blocks_fam as bf
        from . import projgen
        try:
            d = bf.load_circuit(circuit)
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"device": d.get("device"), "parts": bf.parts_of(projgen.build(d))}

    @app.get("/api/blocks/example/{name}")
    def blocks_example(name: str):
        f = EXAMPLES / "blocks" / f"{name}.json"
        if not f.is_file():
            f = EXAMPLES / "blocks" / "families" / f"{name}.json"
        if not name.replace("_", "").isalnum() or not f.is_file():
            raise HTTPException(404)
        return json.loads(f.read_text(encoding="utf-8"))

    @app.post("/api/blocks/generate")
    def blocks_generate(req: BlocksReq):
        from . import blocks as bl
        from . import families as FAM
        if req.circuit or FAM.is_family(req.device):
            from .families import blocks_fam as bf
            ws = dict(req.workspace, device=req.device, circuit=req.circuit, gen_mode=req.gen_mode)
            fam = FAM.family_of(req.device or "")
            # AVR/STM32 builds take ~1 s: compile on every edit; ESP32 (arduino-cli, slow) = checker only here
            r = bf.generate(ws, cur(), compile=fam in ("avr", "stm32"))
            r.pop("project", None)
            if fam == "esp32" and not r.get("error"):
                r["warnings"].append("ESP32: checked only here; Circuit tab > Compile runs arduino-cli")
            return r
        return bl.generate(req.workspace, cur())

    # ------------------------------------------------------------ circuit (M6)
    @app.get("/api/circuit/meta")
    def circuit_meta(device: str = "PIC24FJ256GB110"):
        from . import circuit as cir
        try:
            return cir.meta(device, cur().mplab_packs_dir)
        except (KeyError, FileNotFoundError, ValueError) as e:
            raise HTTPException(404, f"no pin data for {device}: {e}")

    @app.get("/api/circuit/examples")
    def circuit_examples():
        out = []
        for f in sorted(EXAMPLES.glob("circuit/*.json")) + sorted(EXAMPLES.glob("circuit/families/*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"name": f.stem, "title": d.get("title", f.stem), "device": d.get("device", ""),
                        "family": d.get("family") or ("pic" if f.parent.name == "circuit" else f.parent.name)})
        return out

    @app.get("/api/circuit/example/{name}")
    def circuit_example(name: str):
        f = EXAMPLES / "circuit" / f"{name}.json"
        if not f.is_file():
            f = EXAMPLES / "circuit" / "families" / f"{name}.json"
        if not name.replace("_", "").isalnum() or not f.is_file():
            raise HTTPException(404)
        return json.loads(f.read_text(encoding="utf-8"))

    @app.post("/api/circuit/check")
    def circuit_check(req: CircuitReq):
        from . import circuit as cir
        return cir.check(req.circuit, cur().mplab_packs_dir)

    @app.post("/api/circuit/generate")
    def circuit_generate(req: CircuitReq):
        from . import projgen
        from .projgen import syntax
        from .compiler import compile_project
        s = cur()
        res = projgen.build(req.circuit, getattr(s, "ics", "PGx2") or "PGx2", s.mplab_packs_dir)
        if not res.get("erc_ok", True):
            return {k: v for k, v in res.items() if k != "amalg"}
        if res.get("family"):  # M8: AVR / STM32 / ESP32 -> box/PC toolchain, no XC16 syntax pass
            if req.compile:
                from .families import toolchain as fam_tc
                cr = fam_tc.compile_result(res)
                hx = cr.pop("hex", None)
                res["compile"] = cr
                if hx and cr.get("hex_name", "").endswith(".hex"):
                    res["hex"] = hx.decode(errors="replace")
            return {k: v for k, v in res.items() if k != "amalg"}
        res["syntax"] = syntax.syntax_check(res, projgen.load_dev(res["device"]))
        if req.compile:
            cr = compile_project(res, s)
            res["compile"] = {k: v for k, v in cr.to_dict().items() if k != "hex_path"}
            if cr.hex_path and Path(cr.hex_path).is_file():
                res["hex"] = Path(cr.hex_path).read_text(errors="replace")
        return {k: v for k, v in res.items() if k != "amalg"}

    # ------------------------------------------------------------ schematic (M7, KiCad-style)
    @app.post("/api/circuit/netlist")
    def circuit_netlist(req: CircuitReq):
        from . import schematic as sch
        d = sch.migrate(req.circuit)
        if req.fmt == "json":
            return sch.netlist_json(d, cur().mplab_packs_dir)
        name = (d.get("name") or "circuit").replace(" ", "_")
        return Response(sch.kicad_net(d, cur().mplab_packs_dir), media_type="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{name}.net"'})

    @app.post("/api/circuit/autowire")
    def circuit_autowire(req: CircuitReq):
        from . import schematic as sch
        return sch.autowire(req.circuit, req.part, cur().mplab_packs_dir)

    @app.post("/api/circuit/route")
    def circuit_route(req: CircuitReq):
        """part='' -> route wires without pts; part='all' -> re-route every wire; part='tidy' -> also re-place parts."""
        from . import geometry as GM, schematic as sch
        d = sch.migrate(req.circuit)
        pk = cur().mplab_packs_dir
        if req.part == "tidy":
            d = GM.tidy(d, None, pk)
        elif req.part == "fix":
            from .rewire import repair
            d = repair(GM.route_all(d, pk, only_unrouted=True), pk)[0]
        elif req.part == "check":
            pass
        else:
            d = GM.route_all(d, pk, only_unrouted=req.part != "all")
        a = GM.audit(d, pk)
        return {"circuit": d, "audit": a}

    @app.post("/api/circuit/rewire")
    def circuit_rewire(req: CircuitReq):
        """M9.2 Auto re-wire: part = new device id (optional).  Remaps MCU pins, rebuilds support parts, re-places
        and re-routes everything; returns {circuit, summary, audit, check}."""
        from . import rewire as RW
        return RW.rewire(req.circuit, cur().mplab_packs_dir, req.part or None)

    @app.post("/api/circuit/checklist")
    def circuit_checklist(req: CircuitReq):
        from . import schematic as sch
        return sch.mcu_checklist(sch.migrate(req.circuit), cur().mplab_packs_dir)

    @app.post("/api/circuit/migrate")
    def circuit_migrate(req: CircuitReq):
        from . import schematic as sch
        return sch.migrate(req.circuit)

    @app.post("/api/circuit/to_blocks")
    def circuit_to_blocks(req: CircuitReq):
        from .projgen.bridge import to_blocks
        return to_blocks(req.circuit, cur().mplab_packs_dir)

    @app.post("/api/circuit/project.zip")
    def circuit_zip(req: CircuitReq):
        from . import projgen
        s = cur()
        res = projgen.build(req.circuit, getattr(s, "ics", "PGx2") or "PGx2", s.mplab_packs_dir)
        if not res.get("erc_ok") or not res.get("files"):
            raise HTTPException(400, "circuit has ERC errors; fix them before generating a project")
        return Response(projgen.zip_bytes(res), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="%s%s.zip"' % (res["name"], "" if res.get("family") else ".X")})

    @app.get("/api/status")
    def status():
        s = cur()
        return {"version": __version__, "device": s.device,
                "meta_key_set": bool(s.meta_api_key),
                "meta_model": s.meta_model,
                "xc16_gcc": find_compiler(s)[1], "compiler": find_compiler(s)[0], "ipecmd": find_ipecmd(s.ipecmd_path),
                "flash_enabled": s.enable_flash, "flash_tool": s.ipecmd_tool, "ipe_part": part_name(s.device),
                "datasheet_found": Path(s.datasheet_pdf).is_file(), "clock_profile": s.clock_profile, "ics": s.ics}

    @app.get("/api/pins/functions")
    def pins_fn():
        from . import pins as pn
        from .uart import RP_PIN, OUTPUT_RP_MAX
        return {"functions": pn.functions(), "rp": [{"rp": k, "pin": p, "input_only": k > OUTPUT_RP_MAX} for k, p in RP_PIN.items()]}

    @app.post("/api/pins/plan")
    def pins_plan(body: dict):
        from . import pins as pn, power as pw
        from . import families as FAM
        s = cur()
        dev = body.get("device") or s.device
        if FAM.is_family(dev):
            # M8: family boards - pin functions come from the board table; assignment is done by the Circuit tab
            inf = FAM.info(dev)
            return {"family": FAM.family_of(dev), "device": dev, "rows": [], "board": inf, "power": None,
                    "notes": [f"{dev}: the PIC24 PPS planner does not apply. Use the Circuit tab (auto pin functions "
                              f"from the {FAM.family_of(dev).upper()} board table, with ERC for conflicts, "
                              f"input-only/flash pins and per-pin current limits)."]}
        r = pn.plan(body.get("items") or [], ics=body.get("ics") or s.ics, jtag=bool(body.get("jtag")))
        per = pn.peripherals(r["rows"])
        p = body.get("power") or {}
        r["power"] = pw.estimate(fcy=float(p.get("fcy") or 16e6), peripherals=per, mode=p.get("mode", "run"),
                                 duty=float(p.get("duty", 1.0)), wdt=bool(p.get("wdt")), rtcc=bool(p.get("rtcc")),
                                 battery_mah=float(p["battery_mah"]) if p.get("battery_mah") else None)
        r["power"]["peripherals"] = per
        return r

    @app.post("/api/explain")
    def explain_ep(body: dict):
        from . import explain as ex
        code = body.get("code") or ""
        if not code.strip():
            raise HTTPException(422, "no code")
        ann = ex.explain_code(code)
        ann["explanation"] = None
        if body.get("muse"):
            try:
                from .models import Message
                ann["explanation"] = mk().complete([Message("user", ex.explain_prompt(code, ann))]).strip()
            except ModelError as e:
                ann["explanation_error"] = str(e)
        return ann

    def _fam_boards():
        from . import families as FAM
        return FAM.device_list()

    @app.get("/api/families")
    def families_ep():
        from . import families as FAM
        from .families import toolchain as fam_tc
        return {"families": FAM.FAMILIES, "boards": FAM.device_list(), "toolchains": fam_tc.status()}

    @app.get("/api/doctor")
    def doctor_ep():
        return {"rows": doctor(cur())}

    @app.get("/api/settings")
    def get_settings():
        s = cur()
        profiles = [{"key": k, "title": get_profile(k, s.crystal_hz).title} for k in PROFILE_KEYS]
        return {"settings": public_view(s), "file": str(sfile),
                "options": {"clock_profile": profiles, "ics": list(ICS_PAIRS),
                            "family_mode": ["baremetal", "arduino"], "family_boards": _fam_boards(),
                            "ipecmd_tool": ["PK5", "PK4", "PK3", "ICD4", "ICD5", "SNAP", "PKOB4"],
                            "reasoning_effort": ["minimal", "low", "medium", "high", "xhigh", "max"]}}

    @app.post("/api/settings")
    def post_settings(update: dict):
        try:
            save_settings(update, sfile)
        except ValueError as e:
            raise HTTPException(422, str(e))
        st = dataclasses.replace(base)
        apply_settings_file(st, sfile)
        state["s"] = st
        return get_settings()

    @app.post("/api/detect")
    def detect():
        s = cur()
        return {"xc16_gcc": find_tool("xc16-gcc", ""), "xc16_bin2hex": find_tool("xc16-bin2hex", ""),
                "xc8_cc": find_tool("xc8-cc", "", "xc8"), "xc32_gcc": find_tool("xc32-gcc", "", "xc32"),
                "xc_dsc_gcc": find_tool("xc-dsc-gcc", "", "xc-dsc"),
                "mplab_packs": edcmod.default_pack_roots(),
                "ipecmd": find_ipecmd(""), "configured": {"xc16_gcc": s.xc16_gcc, "ipecmd_path": s.ipecmd_path}}

    @app.get("/api/devices")
    def devices(q: str = "", limit: int = 200):
        s = cur()
        from . import families as FAM
        qq = q.strip().lower()
        fam = [{"name": d["id"], "family": d["title"], "compiler": d["family"]} for d in FAM.device_list()
               if not qq or qq in (d["id"] + d["title"]).lower()]
        return {"roots": edcmod.pack_roots(s.mplab_packs_dir),
                "devices": fam + [{k: v for k, v in d.items() if k != "path"}
                            for d in edcmod.search_devices(q, s.mplab_packs_dir, max(1, min(limit, 2000)))]}

    @app.get("/api/device")
    def device_ep(name: str = ""):
        s = cur()
        from . import families as FAM
        if FAM.is_family(name or s.device):
            b = FAM.board(name or s.device)
            return {"name": b["id"], "family": b["family"].upper() + " · " + b["mcu"], "compiler": b["family"] == "stm32" and "arm-gcc" or "avr-gcc/arduino-cli",
                    "source": "family plugin", "settings": 0, "sfrs": 0, "board": True, "title": b["title"], "pragmas": ""}
        dev = chipdb.get_device(name or s.device, s.mplab_packs_dir, s.cache_dir)
        if dev is None:
            raise HTTPException(404, f"no EDC data for {name or s.device} (check MPLAB_PACKS_DIR)")
        return {"name": dev.name, "family": dev.family, "compiler": dev.compiler, "source": dev.source,
                "program_bytes": dev.program_bytes, "data_bytes": dev.data_bytes, "eeprom_bytes": dev.eeprom_bytes,
                "icsp_pairs": dev.icsp_pairs, "settings": len(dev.visible_settings()), "sfrs": len(dev.sfrs),
                "pragmas": _pragma_text(s, dev)}

    def _pragma_text(s, dev) -> str:
        if chipdb.is_builtin_family(dev.name):
            return render_pragmas(s.profile(), s.ics)
        return pragmamod.render(dev, s.ics)

    @app.get("/api/pragmas")
    def pragmas():
        s = cur()
        if not chipdb.is_builtin_family(s.device):
            dev = chipdb.device_for(s)
            if dev is None:
                raise HTTPException(404, f"no EDC data for {s.device}")
            return {"profile": "edc", "title": f"{dev.name} safe defaults (internal oscillator, WDT off)",
                    "fosc_hz": None, "text": pragmamod.render(dev, s.ics), "clock_code": []}
        p = s.profile()
        return {"profile": p.key, "title": p.title, "fosc_hz": p.fosc_hz, "text": render_pragmas(p, s.ics),
                "clock_code": p.clock_code}

    # ------------------------------------------------------------ generation
    @app.post("/api/calc")
    def calc(req: CalcReq):
        spec = parse_request(req.request)
        if spec.period_s is None:
            raise HTTPException(422, "no period found in request")
        try:
            return {"spec": spec.to_dict(), "solution": solve(spec).to_dict()}
        except ValueError as e:
            raise HTTPException(422, str(e))

    def _iters(req):
        return max(1, min(req.max_iters or cur().max_iters, 10))

    @app.post("/api/generate")
    def generate(req: GenerateReq):
        model = mk()
        res = run_agent(req.request, model, cur(), max_iters=_iters(req), use_images=req.use_images,
                        hint=req.hint, compile_fn=compile_fn, inject_fault=req.inject_fault, context=req.context, context_kind=req.context_kind)
        return res.to_dict()

    @app.post("/api/generate/stream")
    def generate_stream(req: GenerateReq):
        """NDJSON stream: one event per line (spec, model_call, attempt (+diff), done)."""
        model = mk()
        q: queue.Queue = queue.Queue()
        prev = {"code": None}

        def on_event(kind, data):
            if kind == "attempt" and data.get("code"):
                data = dict(data, diff=_diff(prev["code"], data["code"]) if prev["code"] else "")
                prev["code"] = data["code"]
            q.put({"event": kind, "data": data})

        def work():
            try:
                res = run_agent(req.request, model, cur(), max_iters=_iters(req), use_images=req.use_images,
                                hint=req.hint, compile_fn=compile_fn, on_event=on_event,
                                inject_fault=req.inject_fault, context=req.context, context_kind=req.context_kind)
                q.put({"event": "done", "data": res.to_dict()})
            except Exception as e:  # surface unexpected failures to the UI
                q.put({"event": "done", "data": {"status": "error", "error": f"{type(e).__name__}: {e}", "attempts": []}})
            q.put(None)

        threading.Thread(target=work, daemon=True).start()

        def gen():
            while (item := q.get()) is not None:
                yield json.dumps(item, default=str) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    # ------------------------------------------------------------ reverse debug
    @app.post("/api/debug")
    async def debug(dump: str = Form(""), symptom: str = Form(""), code: str = Form(""),
                    fosc_mhz: float | None = Form(None),
                    expected_period_ms: float | None = Form(None), use_model: bool = Form(True),
                    images: list[UploadFile] = File(default=[])):
        imgs = [await f.read() for f in images if f.filename]
        model = None
        if use_model:
            model = mk()
        fosc = fosc_mhz * 1e6 if fosc_mhz else cur().profile().fosc_hz
        res = debug_session(dump, imgs, symptom, code, model, fosc,
                            expected_period_ms / 1e3 if expected_period_ms else None)
        return res.to_dict()

    @app.post("/api/debug/vision")
    async def debug_vision(intent: str = Form(""), code: str = Form(""),
                           fosc_mhz: float | None = Form(None), extraction: str = Form(""), explain: bool = Form(True),
                           device: str = Form(""), passes: int | None = Form(None),
                           images: list[UploadFile] = File(default=[])):
        from .vision_debug import COSC_FOR_PROFILE, normalize, vision_debug
        imgs = [await f.read() for f in images if f.filename]
        ex = None
        if extraction.strip():
            try:
                ex = normalize(json.loads(extraction))
            except (ValueError, TypeError) as e:
                raise HTTPException(422, f"bad extraction JSON: {e}")
        if not imgs and ex is None:
            raise HTTPException(422, "drop, paste or choose an image first")
        model = mk()
        s = cur()
        fosc = fosc_mhz * 1e6 if fosc_mhz else s.profile().fosc_hz
        try:
            rep = vision_debug(imgs, intent, code, model, fosc, COSC_FOR_PROFILE.get(s.clock_profile, 1), s.crystal_hz,
                               extraction=ex, explain=explain, device=device.strip() or s.device,
                               clock_explicit=bool(fosc_mhz), packs_dir=s.mplab_packs_dir, cache_dir=s.cache_dir,
                               passes=max(1, min(3, passes or s.vision_passes)))
        except ModelError as e:
            raise HTTPException(502, str(e))
        return rep.to_dict()

    audit_cache: dict[str, object] = {}

    @app.post("/api/audit")
    async def audit_ep(code: str = Form(""), filename: str = Form("main.c"), explain: bool = Form(False),
                       compile: bool = Form(False), ics: str = Form(""),
                       device: str = Form(""), project: str = Form(""),
                       files: list[UploadFile] = File(default=[]), images: list[UploadFile] = File(default=[])):
        from . import audit as au
        s = cur()
        src: dict[str, str] = {}
        kind = "files"
        for f in files:
            if not f.filename:
                continue
            data = await f.read()
            if f.filename.lower().endswith(".zip"):
                try:
                    src.update(au.load_zip(data))
                except Exception as e:
                    raise HTTPException(422, f"bad zip: {e}")
                kind = "zip"
            elif f.filename.lower().endswith((".c", ".h", ".xml", ".ino", ".cpp")):
                src[Path(f.filename).name] = data.decode("utf-8", "replace")
        if code.strip():
            src[Path(filename or "main.c").name] = code
        model = None
        imgs = [await f.read() for f in images if f.filename]
        notes = []
        if imgs:
            try:
                model = mk()
                src["screenshot.c"] = au.transcribe(imgs, model)
                notes.append("screenshot.c was transcribed from your image by Muse - check it before trusting line numbers.")
            except ModelError as e:
                raise HTTPException(400, f"reading a screenshot needs the Muse model: {e}")
        device = device.strip() or s.device
        kind = project or kind
        if src.get("__device__"):
            device, kind = src.pop("__device__"), "mplabx"
        src.pop("__device__", None)
        for n, t in list(src.items()):
            if n.endswith("configurations.xml"):
                m = re.search(r"<targetDevice>([^<]+)</targetDevice>", t)
                device, kind = (m.group(1).strip() if m else device), "mplabx"
                src.pop(n)
        src = {k: v for k, v in src.items() if k.lower().endswith((".c", ".h", ".ino", ".cpp"))}
        if not src:
            raise HTTPException(422, "paste code, or drop .c/.h files, a project .zip or a screenshot")
        rep = au.audit_files(src, ics=ics or s.ics, crystal_hz=s.crystal_hz, device=device, project=kind)
        rep.notes += notes
        if explain and model is None:
            model = mk()
        au.enrich(rep, model, s, explain=explain and model is not None, compile=compile)
        import uuid
        rid = uuid.uuid4().hex[:12]
        audit_cache[rid] = rep
        while len(audit_cache) > 20:
            audit_cache.pop(next(iter(audit_cache)))
        d = rep.to_dict()
        d["id"] = rid
        d["markdown"] = au.report_markdown(rep)
        return d

    @app.get("/api/audit/{rid}/fixes.zip")
    def audit_fixes(rid: str):
        from . import audit as au
        rep = audit_cache.get(rid)
        if rep is None:
            raise HTTPException(404, "audit expired - run it again")
        return Response(au.fixes_zip(rep), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="chipllama_audit_fixes.zip"'})

    @app.get("/api/audit/examples")
    def audit_examples():
        from . import audit as au
        out = []
        for d in sorted((EXAMPLES / "audit").iterdir()) if (EXAMPLES / "audit").is_dir() else []:
            if d.is_dir():
                files, kind, dev = au.load_path(d)
                out.append({"name": d.name, "kind": kind, "device": dev, "files": files})
        return out

    ex_dir = Path(__file__).resolve().parent.parent / "examples" / "debug"

    @app.get("/api/examples/debug/{name}.png")
    def debug_example_png(name: str):
        f = ex_dir / f"{name}.png"
        if not name.replace("_", "").isalnum() or not f.is_file():
            raise HTTPException(404, "no such example")
        return FileResponse(f, media_type="image/png")

    @app.get("/api/examples/debug/{name}.json")
    def debug_example_meta(name: str):
        if not name.replace("_", "").isalnum() or not (ex_dir / f"{name}.png").is_file():
            raise HTTPException(404, "no such example")
        it = ex_dir / f"{name}.intent.txt"
        code = ex_dir / "toggle_10ms.c" if name.startswith("scope") else None
        return {"intent": it.read_text(encoding="utf-8").strip() if it.is_file() else "",
                "code": code.read_text(encoding="utf-8") if code and code.is_file() else ""}

    @app.get("/api/page/{page}.png")
    def page(page: int):
        imgs, note = load_page_images([page], cur())
        if not imgs:
            raise HTTPException(404, note)
        return Response(imgs[0][1], media_type="image/png")

    # ------------------------------------------------------------ export / flash
    @app.post("/api/export")
    def export(req: ExportReq):
        s = cur()
        spec = parse_request(req.request) if req.request else None
        sol = None
        if spec and spec.period_s:
            if any(n.startswith("no clock stated") for n in spec.notes):
                spec.fosc_hz = s.profile().fosc_hz
            try:
                sol = solve(spec)
            except ValueError:
                pass
        with tempfile.TemporaryDirectory() as td:
            paths = export_project(req.code, td, s.device, spec if sol else None, sol,
                                   profile_title=f"{s.profile().title}; ICS = {s.ics}")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for p in paths:
                    z.write(p, f"ChipLlamaProject/{p.name}")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="ChipLlamaProject.zip"'})

    @app.post("/api/flash")
    def flash_ep(req: FlashReq):
        s = cur()
        if not s.enable_flash:
            return JSONResponse({"status": "disabled", "reason": "Flashing is OFF - enable it in Settings (or CHIPLLAMA_ENABLE_FLASH=1)."})
        if not req.confirm:
            return JSONResponse({"status": "disabled", "reason": "confirm=true required"})
        with tempfile.TemporaryDirectory() as td:
            cr = compile_c(req.code, s, link=True, workdir=td)
            if cr.status != "ok" or not cr.hex_path:
                return {"status": "error", "reason": "build failed or toolchain missing", "compile": cr.to_dict()}
            return flash(cr.hex_path, s, confirmed=True).to_dict()

    return app


def get_app() -> FastAPI:   # uvicorn chipllama.server:get_app --factory
    return create_app()
