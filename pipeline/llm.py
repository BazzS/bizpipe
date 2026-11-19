"""Слой вызова LLM: цепочка провайдеров, ретраи с backoff, строгая валидация JSON.

Решение по сбоям (зафиксировано с заказчиком):
  1. На каждом звене цепочки — N попыток с паузами из config.llm.backoff_seconds.
  2. Ответ, не прошедший валидацию схемы, считается такой же неудачей, как сетевая ошибка.
  3. Звено исчерпано → следующее звено цепочки (другая модель / другой провайдер).
  4. Вся цепочка исчерпана → LLMUnavailable. Вызывающий код ставит модели статус
     "not_verified" — она не попадает в рейтинг и не попадает в отказы (это не её вина).

Изоляция агентов: каждый вызов — отдельный запрос без истории (store=False,
без previous_response_id). Общего контекста между вызовами нет физически.
Во журнал пишется хеш промпта и список полей входа — по нему видно, что кому показали.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .config import load_config, secret
from .logger import log_event

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """Все звенья цепочки провайдеров исчерпаны."""


class ProviderNotConfigured(RuntimeError):
    """Нет ключа или пакета провайдера — ретраить бессмысленно, сразу к следующему звену."""


@dataclass
class LLMResult:
    data: BaseModel
    provider: str
    model: str
    attempts: int                      # сколько попыток всего ушло
    citations: list[dict] = field(default_factory=list)  # ссылки из веб-поиска


def _prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256((system + "\x00" + user).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- провайдеры

def _call_openai(model: str, system: str, user: str, web_search: bool, cfg: dict) -> tuple[str, list[dict], dict]:
    from openai import OpenAI

    key = secret("OPENAI_API_KEY")
    if not key:
        raise ProviderNotConfigured("OPENAI_API_KEY not set")
    client = OpenAI(api_key=key, timeout=cfg["timeout_seconds"], max_retries=0)  # ретраи — наши, не SDK
    kwargs = dict(
        model=model,
        instructions=system,
        input=user + "\n\nRespond with a JSON object.",  # json_object требует слово json во входе
        store=False,                         # ничего не хранится на стороне провайдера
    )
    if web_search:
        # JSON-режим вместе с web_search у OpenAI ломает цитаты (известная проблема) —
        # поэтому с поиском формат текстовый, JSON достаёт _extract_json.
        kwargs["tools"] = [{"type": "web_search"}]
        kwargs["include"] = ["web_search_call.action.sources"]
        if cfg.get("max_tool_calls"):
            kwargs["max_tool_calls"] = cfg["max_tool_calls"]   # платится каждый поиск — ограничиваем
    else:
        kwargs["text"] = {"format": {"type": "json_object"}}
        kwargs["temperature"] = cfg["temperature"]
    resp = client.responses.create(**kwargs)

    citations, searches = [], 0
    for item in resp.output or []:
        if getattr(item, "type", "") == "web_search_call":   # всё, что поиск реально открыл
            searches += 1
            for src in getattr(getattr(item, "action", None), "sources", None) or []:
                if getattr(src, "url", None):
                    citations.append({"url": src.url, "title": getattr(src, "title", "")})
        for part in getattr(item, "content", None) or []:
            for ann in getattr(part, "annotations", None) or []:
                if getattr(ann, "type", "") == "url_citation":
                    citations.append({"url": ann.url, "title": getattr(ann, "title", "")})
    u = getattr(resp, "usage", None)
    usage = {"input_tokens": getattr(u, "input_tokens", 0) or 0, "output_tokens": getattr(u, "output_tokens", 0) or 0,
             "searches": searches}
    return resp.output_text, citations, usage


def _call_anthropic(model: str, system: str, user: str, web_search: bool, cfg: dict) -> tuple[str, list[dict], dict]:
    try:
        import anthropic  # ставится отдельно, когда появится ключ
    except ImportError as e:
        raise ProviderNotConfigured("anthropic package not installed") from e

    key = secret("ANTHROPIC_API_KEY")
    if not key:
        raise ProviderNotConfigured("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=key, timeout=cfg["timeout_seconds"], max_retries=0)
    kwargs = dict(model=model, max_tokens=4096, system=system,
                  messages=[{"role": "user", "content": user}])
    if web_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    resp = client.messages.create(**kwargs)
    text, citations = "", []
    for block in resp.content:
        if block.type == "text":
            text += block.text
            for c in getattr(block, "citations", None) or []:
                if getattr(c, "url", None):
                    citations.append({"url": c.url, "title": getattr(c, "title", "")})
    u = resp.usage
    usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
             "searches": getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0}
    return text, citations, usage


PROVIDERS = {"openai": _call_openai, "anthropic": _call_anthropic}


COST = {"usd": 0.0, "calls": 0, "searches": 0}   # накопительно за запуск


def estimate_cost(model: str, usage: dict, prices: dict) -> float:
    p = prices.get(model, {})
    return (usage.get("input_tokens", 0) * p.get("input_per_m", 0) / 1e6
            + usage.get("output_tokens", 0) * p.get("output_per_m", 0) / 1e6
            + usage.get("searches", 0) * prices.get("web_search_per_call", 0))


def _extract_json(text: str) -> dict:
    """Модели иногда оборачивают JSON в ```json ... ```. Достаём первый объект."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in response")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- публичный API

def call_json(
    *,
    task: str,
    system: str,
    user: str,
    schema: type[T],
    input_fields: list[str],
    subject_id: str,
    web_search: bool = False,
) -> LLMResult:
    """Один изолированный вызов LLM с валидацией ответа по pydantic-схеме.

    task         — имя шага для журнала (filter/devil/verifier/...)
    input_fields — какие поля данных попали в промпт (для аудита изоляции)
    subject_id   — id бизнес-модели, к которой относится вызов
    """
    cfg = load_config()["llm"]
    backoff = cfg["backoff_seconds"]
    system = system + "\n\nReturn ONLY a JSON object matching this JSON Schema:\n" + json.dumps(schema.model_json_schema())
    phash = _prompt_hash(system, user)
    total_attempts = 0

    chain = (cfg.get("task_chains") or {}).get(task, cfg["chain"])   # можно задать модель для отдельного шага
    for link in chain:
        provider, model, retries = link["provider"], link["model"], link["retries"]
        fn = PROVIDERS.get(provider)
        if fn is None:
            log_event("llm", task=task, subject_id=subject_id, provider=provider, model=model,
                      status="skipped", error="unknown provider")
            continue
        for attempt in range(1, retries + 1):
            total_attempts += 1
            t0 = time.monotonic()
            try:
                out = fn(model, system, user, web_search, cfg)
                text, citations = out[0], out[1]
                usage = out[2] if len(out) > 2 else {}
                cost = estimate_cost(model, usage, cfg.get("prices", {}))
                COST["usd"] += cost            # платим и за невалидный ответ
                COST["calls"] += 1
                COST["searches"] += usage.get("searches", 0)
                data = schema.model_validate(_extract_json(text))
            except ProviderNotConfigured as e:
                log_event("llm", task=task, subject_id=subject_id, provider=provider, model=model,
                          attempt=attempt, status="skipped", error=str(e))
                break  # нет ключа/пакета — сразу к следующему звену
            except (ValidationError, ValueError) as e:
                err, kind = str(e)[:500], "invalid_output"
            except Exception as e:  # сеть, лимиты, 5xx
                if getattr(e, "status_code", None) in (400, 401, 403, 404):
                    log_event("llm", task=task, subject_id=subject_id, provider=provider, model=model,
                              attempt=attempt, status="bad_request", error=f"{type(e).__name__}: {e}"[:500])
                    break  # запрос неверный — повтор не поможет, к следующему звену
                err, kind = f"{type(e).__name__}: {e}"[:500], "provider_error"
            else:
                log_event("llm", task=task, subject_id=subject_id, provider=provider, model=model,
                          attempt=attempt, status="ok", prompt_hash=phash, input_fields=input_fields,
                          web_search=web_search, n_citations=len(citations), usage=usage,
                          cost_usd=round(cost, 4), latency_s=round(time.monotonic() - t0, 2))
                return LLMResult(data, provider, model, total_attempts, citations)

            log_event("llm", task=task, subject_id=subject_id, provider=provider, model=model,
                      attempt=attempt, status=kind, error=err, prompt_hash=phash,
                      latency_s=round(time.monotonic() - t0, 2))
            if attempt < retries:
                time.sleep(backoff[min(attempt - 1, len(backoff) - 1)])

    log_event("llm", task=task, subject_id=subject_id, status="chain_exhausted", attempts=total_attempts)
    raise LLMUnavailable(f"{task}/{subject_id}: all providers failed after {total_attempts} attempts")
