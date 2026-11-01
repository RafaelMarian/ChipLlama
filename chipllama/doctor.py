"""Shared toolchain / configuration status (CLI `chipllama doctor` and the dashboard card)."""
from __future__ import annotations

from pathlib import Path

from .compiler import find_tool, find_compiler, COMPILERS
from . import chipdb
from .config import Settings, settings_path
from .flash import find_ipecmd, part_name


def doctor(s: Settings) -> list[dict]:
    comp, gcc = find_compiler(s)
    drv = COMPILERS[comp][0]
    try:
        dev = chipdb.device_for(s)
    except Exception:
        dev = None
    if dev is None:
        dev_row = ("device data", f"{s.device}: no EDC file found in MPLAB packs (set MPLAB_PACKS_DIR)", False, "error", None)
    elif dev.source == "built-in":
        dev_row = ("device data", f"built-in tables ({dev.family}, {comp.upper()}) - no MPLAB pack found", True, "warn", None)
    else:
        dev_row = ("device data", f"EDC {Path(dev.source).name} · {dev.family} · {comp.upper()} · "
                                  f"{len(dev.visible_settings())} config settings · {len(dev.sfrs)} SFRs", True, "ok", dev.source)
    ipe = find_ipecmd(s.ipecmd_path)
    try:
        prof = s.profile()
        prof_txt, prof_ok = f"{prof.title}; ICS={s.ics}", True
    except ValueError as e:
        prof_txt, prof_ok = str(e), False
    pdf_ok = Path(s.datasheet_pdf).is_file()
    sp = settings_path()
    # (label, value, ok, level, path)  level: ok | warn | info | error
    try:
        from . import dfp
        dfp_val, dfp_warn = dfp.select(comp, s.device, gcc, s.mplab_packs_dir) if gcc else (None, "")
    except Exception:
        dfp_val, dfp_warn = None, ""
    rows = [
        ("device", f"{s.device}  (ipecmd -P{part_name(s.device)})", True, "ok", None),
        dev_row,
        ("model", f"meta · {s.meta_model}", True, "ok", None),
        ("MODEL_API_KEY", "set" if s.meta_api_key else "NOT set",
         bool(s.meta_api_key),
         "ok" if s.meta_api_key else "error", None),
        ("clock profile", prof_txt, prof_ok, "ok" if prof_ok else "error", None),
        (drv, gcc or "NOT FOUND (compile step skipped)", bool(gcc), "ok" if gcc else "warn", gcc),
        *([("DFP (-mdfp)", dfp_warn or dfp_val, not dfp_warn, "warn" if dfp_warn else "ok", dfp_val)] if (dfp_val or dfp_warn) else []),
        ("datasheet PDF", ("found: " if pdf_ok else "missing (text-only prompts): ") + str(s.datasheet_pdf),
         pdf_ok, "ok" if pdf_ok else "warn", str(s.datasheet_pdf)),
        ("flash", f"{'ENABLED' if s.enable_flash else 'off'} · tool={s.ipecmd_tool} · power-target={'yes' if s.ipecmd_power_target else 'no'}"
                  f" · ipecmd={ipe or 'not found'}", (not s.enable_flash) or bool(ipe),
         ("ok" if ipe else "error") if s.enable_flash else "info", ipe),
        ("settings.json", str(sp) + (" (exists)" if sp.is_file() else " (not created yet)"), True,
         "ok" if sp.is_file() else "info", str(sp)),
    ]
    out = [{"label": a, "value": b, "ok": c, "level": d, "path": e} for a, b, c, d, e in rows]
    try:  # M8: other MCU families (optional; read-only detection, never installs anything)
        from .families import toolchain as fam_tc
        for r in fam_tc.status():
            out.append({"label": f"[{r['family']}] {r['name']}",
                        "value": r["path"] or f"not found (optional, for {r['need']}) — install: {r['fix']}",
                        "ok": True, "level": "ok" if r["ok"] else "info", "path": r["path"] or None,
                        "fix": r["fix"], "family": r["family"]})
    except Exception:
        pass
    return out
