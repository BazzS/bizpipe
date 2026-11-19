"""Общие структуры данных. Паспорт утверждения — центральная сущность."""
from __future__ import annotations

import re
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel, Field


class Method(str, Enum):
    primary = "primary"                # первоисточник: сайт компании, пресс-релиз, отчёт
    calculation = "calculation"        # расчёт из других утверждений
    model_judgment = "model_judgment"  # суждение LLM без внешнего подтверждения
    needs_human = "needs_human"        # противоречие или не удалось проверить


class Claim(BaseModel):
    """Паспорт утверждения: что утверждается, откуда, когда, каким методом, насколько уверены."""
    text: str
    source_url: str | None = None
    source_date: str | None = None      # дата источника в ISO, допускается неполная: "2025" или "2025-03"
    method: Method
    confidence: float = Field(ge=0.0, le=1.0)
    conflicting_values: list[str] = []  # если источники расходятся — все версии здесь
    note: str = ""                      # почему понижены метод/уверенность и т.п.


class BusinessModel(BaseModel):
    """Одна бизнес-модель на входе конвейера (сырые данные из источника)."""
    id: str
    name: str
    one_liner: str
    description: str
    website: str | None = None
    source_url: str              # откуда взяли запись
    fetched_at: str              # когда взяли (ISO)
    industry: str
    tags: list[str] = []
    locations: str = ""
    batch: str = ""


# ------------------------------------------------------------ очистка ответов с веб-поиском
# Модели с веб-поиском вставляют ссылки прямо в текст: «… ([site.com](https://site.com/x?utm_source=openai))».
# В карточке это мусор, а сама ссылка нужна в паспорте. Чистим текст и достаём из него URL.

_MD_LINK = re.compile(r"\s*\(?\[([^\]]*)\]\((https?://[^)\s]+)\)\)?")
_BARE_URL = re.compile(r"https?://[^\s)\]]+")
_TRACKING = {"trk", "ref", "fbclid", "gclid", "mc_cid", "mc_eid"}


def clean_url(url: str | None) -> str | None:
    """Убирает markdown-обёртку и трекинговые параметры. «([a.com](https://a.com/x?utm_source=y))» → https://a.com/x"""
    if not url:
        return url
    m = _MD_LINK.search(url) or _BARE_URL.search(url)
    if m:
        url = m.group(2) if m.re is _MD_LINK else m.group(0)
    p = urlparse(url.strip())
    if not p.scheme:
        return url.strip() or None
    q = [(k, v) for k, v in parse_qsl(p.query.replace("&amp;", "&"))
         if not (k.lower().startswith("utm_") or k.lower() in _TRACKING)]
    return urlunparse(p._replace(query=urlencode(q)))


def strip_citations(text: str) -> tuple[str, list[str]]:
    """Возвращает текст без вставленных markdown-ссылок и список вынутых URL (очищенных)."""
    urls = [clean_url(m.group(2)) for m in _MD_LINK.finditer(text)]
    cleaned = _MD_LINK.sub("", text)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip(), urls
