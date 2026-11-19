"""Шаг 2: фильтр по критериям заказчика.

Два вызова LLM на модель:
  1. screen — критерии с check: llm, одним запросом по описанию (дёшево).
  2. analog — критерий big_analog, с веб-поиском. Запускается, только если 1-й прошёл
     (filter.short_circuit). LLM только собирает цифры со ссылками; сравнение с порогом,
     выбор консервативного значения и поиск противоречий делает код — method="calculation".

Статусы модели: passed / rejected / not_verified (сбой LLM — не вина модели, в отказы не идёт).
"""
from __future__ import annotations

import json
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel

from .config import ROOT, load_config
from .llm import LLMUnavailable, call_json
from .logger import log_event
from .schemas import VALUE_KIND_RU, BusinessModel, Claim, Method, clean_url, fmt_usd

Verdict = Literal["pass", "fail", "uncertain"]


# ------------------------------------------------------------ что возвращает LLM

class LLMCriterionVerdict(BaseModel):
    criterion_id: str
    verdict: Verdict
    reason: str
    evidence: Claim


class ScreenOutput(BaseModel):
    verdicts: list[LLMCriterionVerdict]


class ValuationPoint(BaseModel):
    value_usd: float
    kind: Literal["market_cap", "last_round", "press_estimate"]
    source_url: str
    source_date: str | None = None


class AnalogOutput(BaseModel):
    analog_name: str | None
    same_model_reason: str = ""
    values: list[ValuationPoint] = []


# ------------------------------------------------------------ что храним мы

class CriterionResult(BaseModel):
    criterion_id: str
    verdict: Verdict          # что сказала LLM / посчитал код
    passed: bool              # итог с учётом политики on_uncertain
    flagged: bool = False     # пропущена под сомнением → нужен человек
    reason: str
    claim: Claim


class FilterResult(BaseModel):
    model_id: str
    name: str
    status: Literal["passed", "rejected", "not_verified"]
    failed_criteria: list[str] = []
    results: list[CriterionResult] = []
    analog: dict | None = None
    error: str | None = None


# ------------------------------------------------------------ логика

def _apply_policy(verdict: Verdict, criterion: dict, default: str) -> tuple[bool, bool]:
    """→ (passed, flagged)"""
    if verdict == "pass":
        return True, False
    if verdict == "fail":
        return False, False
    policy = criterion.get("on_uncertain", default)
    return (True, True) if policy == "pass_flagged" else (False, False)


def _same_url(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return pa.netloc.removeprefix("www.") == pb.netloc.removeprefix("www.") and \
        pa.path.rstrip("/") == pb.path.rstrip("/")


def judge_analog(out: AnalogOutput, citations: list[dict], cfg: dict) -> tuple[Verdict, str, Claim, dict]:
    """Чистая функция: порог, противоречия, проверка ссылок. Без LLM — тестируется напрямую."""
    cited = [c["url"] for c in citations]
    points = []
    for v in out.values:
        # URL, которого нет в результатах поиска, мог быть выдуман моделью
        url = clean_url(v.source_url) or v.source_url
        in_search = any(_same_url(url, u) for u in cited) if cited else None
        points.append({**v.model_dump(), "source_url": url, "in_search_results": in_search})

    info = {"analog_name": out.analog_name, "same_model_reason": out.same_model_reason, "values": points}
    if not out.analog_name or not points:
        claim = Claim(text="Не найдено компании с той же моделью и известной оценкой",
                      method=Method.model_judgment, confidence=0.3)
        return "uncertain", "не найден аналог с той же моделью и известной оценкой", claim, info

    # противоречивые данные: берём консервативное (минимальное) значение, все версии сохраняем
    trusted = [p for p in points if p["in_search_results"] is not False] or points
    lo = min(trusted, key=lambda p: p["value_usd"])
    hi = max(trusted, key=lambda p: p["value_usd"])
    conflict = hi["value_usd"] > lo["value_usd"] * cfg["conflict_ratio"]
    threshold = cfg["min_valuation_usd"] * cfg["tolerance"]
    passed = lo["value_usd"] >= threshold

    conf = 0.8 if lo["in_search_results"] else 0.5
    note = []
    if lo["in_search_results"] is False:
        note.append("source URL not found in web-search results — possibly hallucinated")
        conf = 0.3
    if conflict:
        conf = min(conf, load_config()["conflicts"]["confidence_cap"])
        note.append(f"sources disagree >{cfg['conflict_ratio']}x; conservative value used")

    claim = Claim(
        text=f"{out.analog_name}: {fmt_usd(lo['value_usd'])} ({VALUE_KIND_RU.get(lo['kind'], lo['kind'])})",
        source_url=lo["source_url"], source_date=lo["source_date"],
        method=Method.needs_human if conflict else Method.calculation,
        confidence=conf,
        conflicting_values=[f"{fmt_usd(p['value_usd'])} {VALUE_KIND_RU.get(p['kind'], p['kind'])} {p['source_url']}" for p in points] if conflict else [],
        note="; ".join(note),
    )
    info.update(conservative_usd=lo["value_usd"], conflict=conflict, threshold_usd=threshold)
    verdict: Verdict = "pass" if passed else "fail"
    reason = (f"аналог {out.analog_name}: {fmt_usd(lo['value_usd'])} {'≥' if passed else '<'} порога {fmt_usd(threshold)}"
              + (" (консервативная из расходящихся оценок)" if conflict else ""))
    return verdict, reason, claim, info


def _model_text(m: BusinessModel) -> str:
    return (f"Name: {m.name}\nOne-liner: {m.one_liner}\nDescription: {m.description}\n"
            f"Tags: {', '.join(m.tags)}\nWebsite: {m.website}\nLocations: {m.locations}\n"
            f"Source URL: {m.source_url}\nFetch date: {m.fetched_at[:10]}")


INPUT_FIELDS = ["name", "one_liner", "description", "tags", "website", "locations", "source_url", "fetched_at"]


def screen_model(m: BusinessModel) -> FilterResult:
    full = load_config()
    fcfg = full["filter"]
    criteria = {c["id"]: c for c in fcfg["criteria"]}
    llm_crit = [c for c in fcfg["criteria"] if c["check"] == "llm"]
    results: list[CriterionResult] = []

    try:
        # --- критерии 1–4
        system = (ROOT / "prompts" / "screen.md").read_text().format(
            jurisdiction=", ".join(full["jurisdiction"]),
            criteria="\n".join(f"- {c['id']}: {c['text']}" for c in llm_crit))
        r = call_json(task="filter.screen", system=system, user=_model_text(m), schema=ScreenOutput,
                      input_fields=INPUT_FIELDS, subject_id=m.id)
        got = {v.criterion_id: v for v in r.data.verdicts}
        for c in llm_crit:
            v = got.get(c["id"])
            if v is None:  # LLM пропустила критерий — не угадываем, считаем «не уверена»
                v = LLMCriterionVerdict(criterion_id=c["id"], verdict="uncertain", reason="LLM не ответила по этому критерию",
                                        evidence=Claim(text="-", method=Method.needs_human, confidence=0))
            passed, flagged = _apply_policy(v.verdict, c, fcfg["on_uncertain"])
            if flagged:
                v.evidence.method = Method.needs_human
            results.append(CriterionResult(criterion_id=c["id"], verdict=v.verdict, passed=passed,
                                           flagged=flagged, reason=v.reason, claim=v.evidence))

        analog = None
        if all(x.passed for x in results) or not fcfg["short_circuit"]:
            # --- критерий 5: аналог $300M+
            user = f"Business model:\n{m.one_liner}\n{m.description}\nStartup: {m.name} ({m.website})"
            r = call_json(task="filter.analog", system=(ROOT / "prompts" / "analog.md").read_text(), user=user,
                          schema=AnalogOutput, input_fields=["name", "one_liner", "description", "website"],
                          subject_id=m.id, web_search=True)
            verdict, reason, claim, analog = judge_analog(r.data, r.citations, full["big_analog"])
            c = criteria["big_analog"]
            passed, flagged = _apply_policy(verdict, c, fcfg["on_uncertain"])
            results.append(CriterionResult(criterion_id="big_analog", verdict=verdict, passed=passed,
                                           flagged=flagged, reason=reason, claim=claim))
    except LLMUnavailable as e:
        return FilterResult(model_id=m.id, name=m.name, status="not_verified", results=results, error=str(e))

    failed = [x.criterion_id for x in results if not x.passed]
    return FilterResult(model_id=m.id, name=m.name, status="rejected" if failed else "passed",
                        failed_criteria=failed, results=results, analog=analog)


def run_filter() -> list[FilterResult]:
    models = [BusinessModel(**d) for d in json.loads((ROOT / "data" / "models.json").read_text(encoding="utf-8"))]
    out = []
    for m in models:
        res = screen_model(m)
        out.append(res)
        log_event("filter", subject_id=m.id, status=res.status, failed=res.failed_criteria,
                  flagged=[x.criterion_id for x in res.results if x.flagged])
        if res.status == "rejected":
            first = next(x for x in res.results if not x.passed)
            log_event("rejected", subject_id=m.id, name=m.name, criterion=first.criterion_id,
                      all_failed=res.failed_criteria, reason=first.reason,
                      evidence=first.claim.model_dump())
    (ROOT / "data" / "filter_results.json").write_text(
        json.dumps([r.model_dump() for r in out], ensure_ascii=False, indent=2), encoding="utf-8")
    return out
