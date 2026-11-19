"""Перевод текста отчёта на русский.

Агенты работают по-английски: факты проверяются по англоязычным источникам, и переводить их до проверки нельзя —
агент Б должен видеть утверждение в той форме, в какой его можно найти. Переводится только то, что попадает
в отчёт для собственника: один вызов дешёвой модели на всё, перевод кешируется в data/translations_ru.json.
Сбой LLM → в отчёте остаётся оригинал (отчёт важнее перевода).
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel

from .config import ROOT
from .llm import LLMUnavailable, call_json
from .logger import log_event

CACHE = ROOT / "data" / "translations_ru.json"
SYSTEM = ("Translate each item into Russian for a business owner. Keep company and product names, numbers, "
          "currencies and dates exactly as they are. Plain, short business Russian, no comments. "
          "Return every item with the same index i.")
BATCH = 40


class Item(BaseModel):
    i: int
    ru: str


class Translation(BaseModel):
    items: list[Item]


def needs_ru(text: str | None) -> bool:
    """Латинских слов больше, чем кириллических, и это не просто название/ссылка."""
    if not text or text.startswith("http"):
        return False
    latin = len(re.findall(r"\b[A-Za-z]{3,}\b", text))
    cyr = len(re.findall(r"[А-Яа-яЁё]{3,}", text))
    return latin >= 3 and latin > cyr


def translate_all(texts: list[str]) -> dict[str, str]:
    cache: dict[str, str] = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    todo = sorted({t for t in texts if needs_ru(t) and t not in cache})
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        user = "\n".join(json.dumps({"i": i, "text": t}, ensure_ascii=False) for i, t in enumerate(chunk))
        try:
            r = call_json(task="translate", system=SYSTEM, user=user, schema=Translation,
                          input_fields=["report_text"], subject_id=f"report/{start // BATCH}")
        except LLMUnavailable as e:
            log_event("translate", status="skipped", error=str(e), n=len(chunk))
            continue
        for it in r.data.items:
            if 0 <= it.i < len(chunk) and it.ru.strip():
                cache[chunk[it.i]] = it.ru.strip()
    if todo:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return cache
