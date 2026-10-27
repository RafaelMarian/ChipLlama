"""FastAPI app + static single-page UI."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import __version__
from .agent import run_agent
from .compiler import compile_c, find_tool
from .config import Settings
from .datasheet import load_page_images
from .debug import debug_session
from .export import export_project
from .flash import find_ipecmd, flash, part_name
from .models import make_model, ModelError
from .timers import parse_request, solve

WEB = Path(__file__).parent / "web"


class GenerateReq(BaseModel):
    request: str
    backend: str | None = None
    max_iters: int = 4
    use_images: bool = True
    hint: bool = False


class CalcReq(BaseModel):
    request: str


class ExportReq(BaseModel):
    code: str
    request: str = ""


class FlashReq(BaseModel):
    code: str
    confirm: bool = False


def create_app(settings: Settings | None = None, model_factory: Callable | None = None,
               compile_fn: Callable = compile_c) -> FastAPI:
    s = settings or Settings.from_env()
    mk = model_factory or (lambda backend: make_model(s, backend))
    app = FastAPI(title="ChipLlama", version=__version__)

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/status")
    def status():
        return {"version": __version__, "device": s.device, "backend": s.backend,
                "meta_key_set": bool(s.meta_api_key), "baseten_key_set": bool(s.baseten_api_key),
                "meta_model": s.meta_model, "baseten_model": s.baseten_model,
                "xc16_gcc": find_tool("xc16-gcc", s.xc16_gcc), "ipecmd": find_ipecmd(s.ipecmd_path),
                "flash_enabled": s.enable_flash, "flash_tool": s.ipecmd_tool, "ipe_part": part_name(s.device),
                "datasheet_found": Path(s.datasheet_pdf).is_file()}

    @app.post("/api/calc")
    def calc(req: CalcReq):
        spec = parse_request(req.request)
        if spec.period_s is None:
            raise HTTPException(422, "no period found in request")
        try:
            return {"spec": spec.to_dict(), "solution": solve(spec).to_dict()}
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/generate")
    def generate(req: GenerateReq):
        try:
            model = mk(req.backend)
        except ValueError as e:
            raise HTTPException(400, str(e))
        res = run_agent(req.request, model, s, max_iters=max(1, min(req.max_iters, 8)), use_images=req.use_images,
                        hint=req.hint, compile_fn=compile_fn)
        return res.to_dict()

    @app.post("/api/debug")
    async def debug(dump: str = Form(""), symptom: str = Form(""), code: str = Form(""),
                    backend: str | None = Form(None), fosc_mhz: float = Form(32.0),
                    expected_period_ms: float | None = Form(None), use_model: bool = Form(True),
                    images: list[UploadFile] = File(default=[])):
        imgs = [await f.read() for f in images if f.filename]
        model = None
        if use_model:
            try:
                model = mk(backend)
            except ValueError as e:
                raise HTTPException(400, str(e))
        res = debug_session(dump, imgs, symptom, code, model, fosc_mhz * 1e6,
                            expected_period_ms / 1e3 if expected_period_ms else None)
        return res.to_dict()

    @app.get("/api/page/{page}.png")
    def page(page: int):
        imgs, note = load_page_images([page], s)
        if not imgs:
            raise HTTPException(404, note)
        return Response(imgs[0][1], media_type="image/png")

    @app.post("/api/export")
    def export(req: ExportReq):
        spec = parse_request(req.request) if req.request else None
        sol = None
        if spec and spec.period_s:
            try:
                sol = solve(spec)
            except ValueError:
                pass
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            paths = export_project(req.code, td, s.device, spec if sol else None, sol)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for p in paths:
                    z.write(p, f"ChipLlamaProject/{p.name}")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="ChipLlamaProject.zip"'})

    @app.post("/api/flash")
    def flash_ep(req: FlashReq):
        if not s.enable_flash:
            return JSONResponse({"status": "disabled", "reason": "Flashing is OFF (CHIPLLAMA_ENABLE_FLASH=0)."})
        if not req.confirm:
            return JSONResponse({"status": "disabled", "reason": "confirm=true required"})
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cr = compile_c(req.code, s, link=True, workdir=td)
            if cr.status != "ok" or not cr.hex_path:
                return {"status": "error", "reason": "build failed or toolchain missing", "compile": cr.to_dict()}
            return flash(cr.hex_path, s, confirmed=True).to_dict()

    return app


def get_app() -> FastAPI:   # uvicorn chipllama.server:get_app --factory
    return create_app()
