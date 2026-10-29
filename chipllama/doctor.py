"""Shared toolchain / configuration status (CLI `chipllama doctor` and the dashboard card)."""
from __future__ import annotations

from pathlib import Path

from .compiler import find_tool
from .config import Settings, settings_path
from .flash import find_ipecmd, part_name


def doctor(s: Settings) -> list[dict]:
    gcc = find_tool("xc16-gcc", s.xc16_gcc)
    ipe = find_ipecmd(s.ipecmd_path)
    try:
        prof = s.profile()
        prof_txt, prof_ok = f"{prof.title}; ICS={s.ics}", True
    except ValueError as e:
        prof_txt, prof_ok = str(e), False
    key = s.meta_api_key if s.backend == "meta" else s.baseten_api_key
    model = s.meta_model if s.backend == "meta" else s.baseten_model
    pdf_ok = Path(s.datasheet_pdf).is_file()
    sp = settings_path()
    meta_need, bt_need = s.backend == "meta", s.backend == "baseten"
    # (label, value, ok, level, path)  level: ok | warn | info | error
    rows = [
        ("device", f"{s.device}  (ipecmd -P{part_name(s.device)})", True, "ok", None),
        ("backend", f"{s.backend} · {model}", True, "ok", None),
        ("MODEL_API_KEY", "set" if s.meta_api_key else ("NOT set" if meta_need else "not set (optional)"),
         bool(s.meta_api_key) or not meta_need,
         "ok" if s.meta_api_key else ("error" if meta_need else "info"), None),
        ("BASETEN_API_KEY", "set" if s.baseten_api_key else ("NOT set" if bt_need else "not set (optional)"),
         bool(s.baseten_api_key) or not bt_need,
         "ok" if s.baseten_api_key else ("error" if bt_need else "info"), None),
        ("clock profile", prof_txt, prof_ok, "ok" if prof_ok else "error", None),
        ("xc16-gcc", gcc or "NOT FOUND (compile step skipped)", bool(gcc), "ok" if gcc else "warn", gcc),
        ("datasheet PDF", ("found: " if pdf_ok else "missing (text-only prompts): ") + str(s.datasheet_pdf),
         pdf_ok, "ok" if pdf_ok else "warn", str(s.datasheet_pdf)),
        ("flash", f"{'ENABLED' if s.enable_flash else 'off'} · tool={s.ipecmd_tool} · power-target={'yes' if s.ipecmd_power_target else 'no'}"
                  f" · ipecmd={ipe or 'not found'}", (not s.enable_flash) or bool(ipe),
         ("ok" if ipe else "error") if s.enable_flash else "info", ipe),
        ("settings.json", str(sp) + (" (exists)" if sp.is_file() else " (not created yet)"), True,
         "ok" if sp.is_file() else "info", str(sp)),
    ]
    _ = key
    return [{"label": a, "value": b, "ok": c, "level": d, "path": e} for a, b, c, d, e in rows]
