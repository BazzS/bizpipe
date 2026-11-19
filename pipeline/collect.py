"""Шаг 1: сбор бизнес-моделей из YC Company Directory (открытый датасет yc-oss/api).

Почему YC: открытый JSON без авторизации, у каждой компании есть описание,
индустрия, локация и ссылка — этого хватает, чтобы LLM поняла модель.
Минус: это стартапы ранней стадии, поэтому критерий «$300M+» проверяется
не по самой компании, а по крупнейшему аналогу на рынке.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import ROOT, load_config
from .logger import log_event
from .schemas import BusinessModel


def fetch_raw(cfg: dict, refresh: bool = False) -> tuple[list[dict], str]:
    """Скачивает датасет (или берёт из кеша). Возвращает записи и время скачивания."""
    cache = ROOT / cfg["cache_path"]
    meta = cache.with_suffix(".meta.json")
    if cache.exists() and meta.exists() and not refresh:
        fetched_at = json.loads(meta.read_text())["fetched_at"]
        return json.loads(cache.read_text(encoding="utf-8")), fetched_at

    resp = httpx.get(cfg["url"], timeout=60, follow_redirects=True)
    resp.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(resp.content)
    fetched_at = datetime.now(timezone.utc).isoformat()
    meta.write_text(json.dumps({"fetched_at": fetched_at, "url": cfg["url"]}))
    return resp.json(), fetched_at


def _cis_country(c: dict, excluded: list[str]) -> str | None:
    """Страна сравнивается целиком, а не подстрокой: иначе «Armenia, Colombia» (город) даст ложное срабатывание.
    Проверяем поле regions и последнюю часть каждой локации «Город, Регион, Страна»."""
    excluded_l = {x.lower(): x for x in excluded}
    countries = {r.strip().lower() for r in c.get("regions") or []}
    for loc in (c.get("all_locations") or "").split(";"):
        if loc.strip():
            countries.add(loc.rsplit(",", 1)[-1].strip().lower())
    hit = countries & excluded_l.keys()
    return excluded_l[min(hit)] if hit else None


def _reject_reason(c: dict, cfg: dict) -> str | None:
    """Технический предфильтр источника (не бизнес-критерии заказчика)."""
    if c.get("status") not in cfg["status"]:
        return f"status={c.get('status')}"
    if c.get("batch") not in cfg["batches"]:
        return "batch not in range"
    if c.get("industry") not in cfg["industries"]:
        return f"industry={c.get('industry')}"
    country = _cis_country(c, cfg["excluded_countries"])
    if country:
        return f"CIS market: {country}"
    if c.get("nonprofit"):
        return "nonprofit"
    if len(c.get("long_description") or "") < cfg["min_description_chars"]:
        return "description too short"
    return None


def sample(raw: list[dict], cfg: dict, seed: int) -> list[dict]:
    """Стратифицированная выборка: из каждой индустрии — столько, сколько указано в конфиге."""
    pool: dict[str, list[dict]] = {ind: [] for ind in cfg["industries"]}
    for c in raw:
        if _reject_reason(c, cfg) is None:
            pool[c["industry"]].append(c)

    rng = random.Random(seed)
    picked = []
    for ind, n in cfg["industries"].items():
        candidates = sorted(pool[ind], key=lambda c: c["id"])  # сортировка → воспроизводимость
        picked += rng.sample(candidates, min(n, len(candidates)))
    return picked[: cfg["sample_size"]]


def to_model(c: dict, fetched_at: str) -> BusinessModel:
    return BusinessModel(
        id=c["slug"],
        name=c["name"],
        one_liner=c.get("one_liner") or "",
        description=c.get("long_description") or "",
        website=c.get("website"),
        source_url=c["url"],
        fetched_at=fetched_at,
        industry=c["industry"],
        tags=c.get("tags") or [],
        locations=c.get("all_locations") or "",
        batch=c.get("batch") or "",
    )


def collect(refresh: bool = False) -> list[BusinessModel]:
    full = load_config()
    cfg = full["source"]
    raw, fetched_at = fetch_raw(cfg, refresh)
    picked = sample(raw, cfg, full["run"]["seed"])
    models = [to_model(c, fetched_at) for c in picked]

    for m in models:
        log_event("collect", subject_id=m.id, name=m.name, industry=m.industry, source_url=m.source_url)
    log_event("collect", summary=True, total_in_source=len(raw), picked=len(models),
              source=cfg["url"], fetched_at=fetched_at)

    out = ROOT / "data" / "models.json"
    out.write_text(json.dumps([m.model_dump() for m in models], ensure_ascii=False, indent=2), encoding="utf-8")
    return models
