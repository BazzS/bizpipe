"""Загрузка config.yaml и секретов из окружения (.env)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@lru_cache
def load_config(path: str | os.PathLike | None = None) -> dict:
    """config.yaml + (опционально) профиль из BIZPIPE_CONFIG, который переопределяет только свои ключи."""
    with open(Path(path) if path else ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    profile = os.getenv("BIZPIPE_CONFIG")
    if profile and not path:
        with open(ROOT / profile, encoding="utf-8") as f:
            cfg = _merge(cfg, yaml.safe_load(f) or {})
    return cfg


def secret(name: str) -> str | None:
    """Ключи берутся только из окружения. В коде и конфиге их нет."""
    value = os.getenv(name)
    return value or None
