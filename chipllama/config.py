"""Settings from environment / .env (tiny loader, no extra dependency)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    path = path or (Path.cwd() / ".env")
    if not path.is_file():
        path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)   # real environment wins over .env


def _b(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    backend: str = "meta"
    meta_api_key: str = ""
    meta_base_url: str = "https://api.meta.ai/v1"
    meta_model: str = "muse-spark-1.3"
    meta_reasoning_effort: str = "high"
    baseten_api_key: str = ""
    baseten_base_url: str = "https://inference.baseten.co/v1"
    baseten_model: str = "meta-models/muse-spark-open"
    baseten_reasoning_effort: str = ""
    max_tokens: int = 16000
    device: str = "PIC24FJ256GB110"
    xc16_gcc: str = ""
    xc16_bin2hex: str = ""
    datasheet_pdf: str = "datasheets/DS39897C.pdf"
    enable_flash: bool = False
    ipecmd_path: str = ""
    ipecmd_tool: str = "PK5"
    ipecmd_serial: str = ""
    ipecmd_power_target: bool = False
    cache_dir: str = ".cache"
    clock_profile: str = "frcpll"
    crystal_hz: float = 8_000_000
    ics: str = "PGx2"
    max_iters: int = 4

    def profile(self):
        from .board import get_profile
        return get_profile(self.clock_profile, self.crystal_hz)

    @classmethod
    def from_env(cls, settings_file: "Path | None" = None) -> "Settings":
        load_dotenv()
        e = os.environ.get
        s = cls._from_environ(e)
        apply_settings_file(s, settings_file)
        return s

    @classmethod
    def _from_environ(cls, e) -> "Settings":
        return cls(
            backend=e("CHIPLLAMA_BACKEND", "meta").strip().lower(),
            meta_api_key=e("MODEL_API_KEY", ""),
            meta_base_url=e("META_BASE_URL", cls.meta_base_url),
            meta_model=e("META_MODEL", cls.meta_model),
            meta_reasoning_effort=e("META_REASONING_EFFORT", cls.meta_reasoning_effort),
            baseten_api_key=e("BASETEN_API_KEY", ""),
            baseten_base_url=e("BASETEN_BASE_URL", cls.baseten_base_url),
            baseten_model=e("BASETEN_MODEL", cls.baseten_model),
            baseten_reasoning_effort=e("BASETEN_REASONING_EFFORT", ""),
            max_tokens=int(e("CHIPLLAMA_MAX_TOKENS", "16000")),
            device=e("CHIPLLAMA_DEVICE", "PIC24FJ256GB110"),
            xc16_gcc=e("XC16_GCC", ""),
            xc16_bin2hex=e("XC16_BIN2HEX", ""),
            datasheet_pdf=e("DATASHEET_PDF", "datasheets/DS39897C.pdf"),
            enable_flash=_b("CHIPLLAMA_ENABLE_FLASH"),
            ipecmd_path=e("IPECMD_PATH", ""),
            ipecmd_tool=e("IPECMD_TOOL", "PK5"),
            ipecmd_serial=e("IPECMD_SERIAL", ""),
            ipecmd_power_target=_b("IPECMD_POWER_TARGET"),
            cache_dir=e("CHIPLLAMA_CACHE", ".cache"),
            clock_profile=e("CHIPLLAMA_CLOCK", "frcpll"),
            crystal_hz=float(e("CHIPLLAMA_CRYSTAL_HZ", "8000000")),
            ics=e("CHIPLLAMA_ICS", "PGx2"),
            max_iters=int(e("CHIPLLAMA_MAX_ITERS", "4")),
        )


# ---------------------------------------------------------------- settings.json (dashboard)
SECRET_FIELDS = ("meta_api_key", "baseten_api_key")
UI_FIELDS = ("device", "clock_profile", "crystal_hz", "ics", "xc16_gcc", "xc16_bin2hex", "ipecmd_path",
             "ipecmd_tool", "ipecmd_serial", "ipecmd_power_target", "enable_flash", "backend", "meta_model",
             "meta_reasoning_effort", "baseten_model", "baseten_reasoning_effort", "max_iters", "max_tokens",
             "datasheet_pdf") + SECRET_FIELDS


def settings_path() -> Path:
    return Path(os.environ.get("CHIPLLAMA_SETTINGS", str(ROOT / "settings.json")))


def apply_settings_file(s: Settings, path: Path | None = None) -> None:
    import json
    path = Path(path) if path else settings_path()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for k, v in data.items():
        if k in UI_FIELDS and v not in (None, ""):
            setattr(s, k, type(getattr(s, k))(v))


def public_view(s: Settings) -> dict:
    """Settings for the UI: secrets reduced to set / not set, never echoed."""
    d = {k: getattr(s, k) for k in UI_FIELDS if k not in SECRET_FIELDS}
    for k in SECRET_FIELDS:
        d[k + "_set"] = bool(getattr(s, k))
    return d


def save_settings(update: dict, path: Path | None = None) -> dict:
    """Merge validated fields into settings.json (never .env). Empty secret = keep existing; '__clear__' removes."""
    import json
    from .board import ICS_PAIRS, PROFILE_KEYS
    path = Path(path) if path else settings_path()
    cur = {}
    if path.is_file():
        try:
            cur = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            cur = {}
    proto = Settings()
    for k, v in update.items():
        if k not in UI_FIELDS:
            raise ValueError(f"unknown setting {k!r}")
        if k in SECRET_FIELDS:
            if v in (None, ""):
                continue
            if v == "__clear__":
                cur.pop(k, None)
                continue
        t = type(getattr(proto, k))
        try:
            v = (v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")) if t is bool else t(v)
        except (TypeError, ValueError):
            raise ValueError(f"bad value for {k}")
        if k == "ics" and v not in ICS_PAIRS:
            raise ValueError(f"ics must be one of {ICS_PAIRS}")
        if k == "clock_profile" and v not in PROFILE_KEYS:
            raise ValueError(f"clock_profile must be one of {PROFILE_KEYS}")
        if k == "backend" and v not in ("meta", "baseten"):
            raise ValueError("backend must be meta or baseten")
        if k == "max_iters" and not 1 <= v <= 10:
            raise ValueError("max_iters must be 1..10")
        if k == "crystal_hz" and not 1e6 <= v <= 48e6:
            raise ValueError("crystal_hz out of range")
        cur[k] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    return cur
