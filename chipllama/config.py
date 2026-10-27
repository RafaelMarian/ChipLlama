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

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        e = os.environ.get
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
        )
