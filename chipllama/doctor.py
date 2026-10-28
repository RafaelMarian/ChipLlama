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
    rows = [
        ("device", f"{s.device} (ipecmd part -P{part_name(s.device)})", True),
        ("backend", f"{s.backend}  model={model}", True),
        ("MODEL_API_KEY", "set" if s.meta_api_key else "NOT set", bool(s.meta_api_key) or s.backend != "meta"),
        ("BASETEN_API_KEY", "set" if s.baseten_api_key else "NOT set", bool(s.baseten_api_key) or s.backend != "baseten"),
        ("clock profile", prof_txt, prof_ok),
        ("xc16-gcc", gcc or "NOT FOUND (compile step will be skipped)", bool(gcc)),
        ("datasheet PDF", f"{s.datasheet_pdf} -> " + ("found" if Path(s.datasheet_pdf).is_file() else "missing (text-only prompts)"),
         Path(s.datasheet_pdf).is_file()),
        ("flash", f"{'ENABLED' if s.enable_flash else 'off'}  tool={s.ipecmd_tool}  power-target={'yes' if s.ipecmd_power_target else 'no'}  "
                  f"ipecmd={ipe or 'not found'}", (not s.enable_flash) or bool(ipe)),
        ("settings.json", str(settings_path()) + (" (exists)" if settings_path().is_file() else " (not created yet)"), True),
    ]
    _ = key
    return [{"label": a, "value": b, "ok": c} for a, b, c in rows]
