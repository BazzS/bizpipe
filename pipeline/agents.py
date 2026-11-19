"""Шаг 3: два независимых агента.

А (devil)    — «почему это не сработает». Видит только данные о модели.
Б (verifier) — проверяет источники top_k самых сильных утверждений А.

Как обеспечена независимость:
  1. Каждый риск А разделён на fact (проверяемое утверждение) и implication (вывод А).
     Б получает только fact + источник — через белый список verifier.visible_fields.
     Вывода, серьёзности, уверенности и категории А он не видит.
  2. Один вызов Б = одно утверждение. Б не видит другие утверждения А и свои прошлые ответы.
  3. Все вызовы без истории (store=False в llm.py), у А и Б разные system-промпты.
  4. Перед вызовом Б код проверяет, что в его промпт не попал ни один скрытый фрагмент А
     (audit_isolation). Нарушение → вызов не уходит, isolation_ok=false. Результат — в журнале.
  5. Итоговую уверенность выставляет код по правилам ниже, а не кто-то из агентов.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from .config import ROOT, load_config
from .filter import FilterResult, _same_url
from .llm import LLMUnavailable, call_json
from .logger import log_event
from .schemas import BusinessModel, Claim, Method, clean_url, strip_citations


# ------------------------------------------------------------ агент А

class Risk(BaseModel):
    category: str
    fact: str
    implication: str
    severity: int = Field(ge=1, le=5)
    passport: Claim


class DevilOutput(BaseModel):
    risks: list[Risk]


# ------------------------------------------------------------ агент Б

VerifyStatus = Literal["confirmed", "partially", "not_confirmed", "contradicted", "source_unavailable"]


class VerifierOutput(BaseModel):
    status: VerifyStatus
    found_source_url: str | None = None
    found_source_date: str | None = None
    is_primary: bool = False
    evidence: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


# ------------------------------------------------------------ итог

class CheckedRisk(BaseModel):
    id: str
    category: str
    fact: str
    implication: str
    severity: int
    strength: float                    # severity × confidence — по ней выбирали top_k
    devil_passport: Claim              # как утверждал А
    verification: VerifierOutput | None = None   # None → не входило в top_k
    final: Claim                       # итоговый паспорт после проверки


class AgentsResult(BaseModel):
    model_id: str
    name: str
    status: Literal["done", "not_verified"]
    risks: list[CheckedRisk] = []
    isolation_ok: bool = True
    error: str | None = None


# ------------------------------------------------------------ изоляция

def project_for_verifier(risk: Risk, visible: list[str]) -> dict:
    """Белый список: из утверждения А в Б уходят только эти поля."""
    full = {"fact": risk.fact, "source_url": risk.passport.source_url, "source_date": risk.passport.source_date}
    return {k: full[k] for k in visible if k in full}


def verifier_prompt(payload: dict) -> str:
    return (f"Statement: {payload.get('fact')}\n"
            f"Attributed source: {payload.get('source_url') or 'none'}\n"
            f"Source date: {payload.get('source_date') or 'unknown'}")


def audit_isolation(payload: dict, prompt: str, risk: Risk, all_risks: list[Risk], visible: list[str]) -> list[str]:
    """Защита на случай будущих правок кода: ищет в промпте Б то, чего там быть не должно."""
    leaks = [f"field:{k}" for k in payload if k not in visible]
    if risk.implication.strip() and risk.implication.strip()[:60] in prompt:
        leaks.append("implication")
    for other in all_risks:
        if other is not risk and other.fact.strip()[:60] in prompt:
            leaks.append("other_claim")
    return leaks


# ------------------------------------------------------------ очистка ответа А

def sanitize_risk(risk: Risk) -> Risk:
    """Вынимает вставленные веб-поиском markdown-ссылки из текста риска.
    Ссылка, найденная в тексте факта, становится источником паспорта, если А его не указал."""
    fact, urls = strip_citations(risk.fact)
    implication, _ = strip_citations(risk.implication)
    url = clean_url(risk.passport.source_url) or (urls[0] if urls else None)
    return risk.model_copy(update={"fact": fact, "implication": implication,
                                   "passport": risk.passport.model_copy(update={"text": fact, "source_url": url})})


def sanitize_verification(v: VerifierOutput) -> VerifierOutput:
    evidence, urls = strip_citations(v.evidence)
    return v.model_copy(update={"evidence": evidence,
                                "found_source_url": clean_url(v.found_source_url) or (urls[0] if urls else None)})


_HOLLOW = re.compile(r"(US\$|USD|\$|€|£)\s*(million|billion|trillion|mn|bn)\b|\b(of|at|by)\s+%", re.I)


def is_hollow(fact: str) -> bool:
    """Факт с вырезанными числами («reach US$ million … CAGR of %») — тизер платного отчёта, проверять нечего."""
    return bool(_HOLLOW.search(fact))


def dedupe_risks(risks: list[Risk]) -> tuple[list[Risk], list[str]]:
    """Одно и то же — один риск. Убирает пустые факты без чисел, дословные повторы и риски с тем же источником,
    что у более сильного (список уже отсортирован А по силе). Три факта с одной страницы — это один риск."""
    kept, dropped, seen_facts, seen_urls = [], [], set(), set()
    for r in risks:
        key = re.sub(r"\W+", " ", r.fact.lower()).strip()
        url = r.passport.source_url
        if is_hollow(r.fact) or key in seen_facts or (url and any(_same_url(url, u) for u in seen_urls)):
            dropped.append(r.fact[:80])
            continue
        seen_facts.add(key)
        if url:
            seen_urls.add(url)
        kept.append(r)
    return kept, dropped


_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(%|percent|trillion|billion|million|thousand|bn|tn|[kmbt])?\b", re.I)
_RANGE = re.compile(r"\$?(\d[\d,]*(?:\.\d+)?)\s*[–-]\s*\$?(\d[\d,]*(?:\.\d+)?)")
_MONTH = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+$", re.I)
_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9,
          "trillion": 1e12, "tn": 1e12, "t": 1e12}


def _amounts(text: str) -> list[tuple[float, bool]]:
    """Числа текста с учётом масштаба: «$14.14 billion» → 1.414e10. Годы и даты не считаются."""
    out = []
    for m in _NUM.finditer(text):
        num, unit = m.group(1), (m.group(2) or "").lower()
        v = float(num.replace(",", ""))
        if not unit and 1900 <= v <= 2100:     # год
            continue
        if not unit and _MONTH.search(text[max(0, m.start() - 12): m.start()]):   # «October 14»
            continue
        out.append((v * _SCALE.get(unit, 1), unit in ("%", "percent")))
    return out


def numbers_disagree(fact: str, evidence: str, tolerance: float = 0.15) -> list[str]:
    """Числа факта, которых нет в подтверждении Б (±15%). Пусто — расхождений нет или сверять нечего."""
    ev = _amounts(evidence)
    if not ev:
        return []
    bad = []
    for lo, hi in _RANGE.findall(fact):        # «$250–$300» подтверждён, если в источнике есть любой из концов
        if any(abs(float(x.replace(",", "")) - e) <= tolerance * max(float(x.replace(",", "")), e)
               for x in (lo, hi) for e, _ in ev):
            fact = fact.replace(lo, "", 1).replace(hi, "", 1)
    for v, pct in _amounts(fact):
        if v < 10 and not pct:                 # «4 регулятора», «3 месяца» — не сверяем
            continue
        if not any(p == pct and abs(v - e) <= tolerance * max(v, e) for e, p in ev):
            bad.append(f"{v:,.4g}{'%' if pct else ''}")
    return bad


def check_verification(fact: str, v: VerifierOutput) -> VerifierOutput:
    """Код не доверяет «confirmed» вслепую: если числа в подтверждении другие — это «partially»."""
    if v.status != "confirmed":
        return v
    bad = numbers_disagree(fact, v.evidence)
    if not bad:
        return v
    return v.model_copy(update={"status": "partially",
                                "evidence": f"{v.evidence} [code: numbers differ from the statement: {', '.join(bad)}]"})


# ------------------------------------------------------------ итоговый паспорт

def finalize(risk: Risk, v: VerifierOutput | None, devil_citations: list[str], cap: float) -> Claim:
    """Правила (чистая функция, тестируется):
    - не проверялось     → model_judgment, ≤cap: независимо никто не подтвердил, поэтому
                           не может поднимать надёжность карточки как первоисточник. Ссылка А сохраняется.
    - confirmed          → источник Б; primary, если Б нашёл первоисточник; уверенность Б
    - partially          → источник Б, needs_human, ≤cap (данные расходятся)
    - contradicted       → needs_human, уверенность самого факта низкая (1 − уверенность Б)
    - not_confirmed / source_unavailable → model_judgment, ≤cap
    """
    p = risk.passport
    if v is None:
        method = Method.model_judgment if p.method in (Method.primary, Method.calculation) else p.method
        url_ok = p.source_url and any(_same_url(p.source_url, u) for u in devil_citations)
        note = ("not verified (outside top_k); source from agent A's search" if url_ok
                else "not verified; source not in agent's search results" if p.source_url
                else "not verified; no source")
        return p.model_copy(update={"method": method, "confidence": min(p.confidence, cap), "note": note})

    base = dict(text=risk.fact, source_url=v.found_source_url or p.source_url,
                source_date=v.found_source_date or p.source_date)
    if v.status == "confirmed":
        return Claim(**base, method=Method.primary if v.is_primary else Method.model_judgment,
                     confidence=v.confidence, note=f"confirmed: {v.evidence}")
    if v.status == "partially":
        return Claim(**base, method=Method.needs_human, confidence=min(v.confidence, cap),
                     conflicting_values=[f"agent A: {p.source_url}", f"agent B: {v.found_source_url}"],
                     note=f"partially: {v.evidence}")
    if v.status == "contradicted":
        return Claim(**base, method=Method.needs_human, confidence=round(1 - v.confidence, 2),
                     conflicting_values=[f"agent A: {p.source_url}", f"agent B: {v.found_source_url}"],
                     note=f"contradicted: {v.evidence}")
    return Claim(text=risk.fact, source_url=p.source_url, source_date=p.source_date, method=Method.model_judgment,
                 confidence=min(p.confidence, cap), note=f"{v.status}: {v.evidence}")


# ------------------------------------------------------------ прогон

def _devil_input(m: BusinessModel, f: FilterResult) -> str:
    analog = f.analog or {}
    return (f"Business model: {m.name} — {m.one_liner}\n{m.description}\nWebsite: {m.website}\n"
            f"Largest analog found: {analog.get('analog_name')} "
            f"(~${(analog.get('conservative_usd') or 0) / 1e6:,.0f}M)")


def run_for_model(m: BusinessModel, f: FilterResult) -> AgentsResult:
    cfg = load_config()["agents"]
    dcfg, vcfg = cfg["devil"], cfg["verifier"]
    cap = cfg["unverified_confidence_cap"]

    # --- агент А
    try:
        system = (ROOT / "prompts" / "devil.md").read_text().format(
            max_risks=dcfg["max_risks"], categories=", ".join(dcfg["categories"]))
        r = call_json(task="agent.devil", system=system, user=_devil_input(m, f), schema=DevilOutput,
                      input_fields=["name", "one_liner", "description", "website", "analog_name", "analog_usd"],
                      subject_id=m.id, web_search=dcfg["web_search"])
    except LLMUnavailable as e:
        return AgentsResult(model_id=m.id, name=m.name, status="not_verified", error=str(e))
    risks, dropped = dedupe_risks([sanitize_risk(x) for x in r.data.risks])
    risks = risks[: dcfg["max_risks"]]
    devil_cites = [c["url"] for c in r.citations]
    log_event("agents", agent="devil", subject_id=m.id, n_risks=len(risks), n_citations=len(devil_cites),
              dropped_duplicates=dropped)

    # --- выбор сильнейших: severity × confidence
    ranked = sorted(enumerate(risks), key=lambda x: x[1].severity * x[1].passport.confidence, reverse=True)
    top = {i for i, _ in ranked[: vcfg["top_k"]]}

    # --- агент Б: отдельный вызов на каждое утверждение
    verifications: dict[int, VerifierOutput] = {}
    isolation_ok = True
    vsystem = (ROOT / "prompts" / "verifier.md").read_text()
    for i in sorted(top):
        payload = project_for_verifier(risks[i], vcfg["visible_fields"])
        prompt = verifier_prompt(payload)
        leaks = audit_isolation(payload, prompt, risks[i], risks, vcfg["visible_fields"])
        log_event("agents", agent="verifier", subject_id=m.id, claim_id=f"r{i + 1}",
                  visible_fields=list(payload), hidden_fields=["implication", "severity", "category", "confidence"],
                  isolation_check="ok" if not leaks else "LEAK", leaks=leaks)
        if leaks:  # вызов не уходит; утверждение останется непроверенным с пометкой
            isolation_ok = False
            continue
        try:
            vr = call_json(task="agent.verifier", system=vsystem, user=prompt, schema=VerifierOutput,
                           input_fields=list(payload), subject_id=f"{m.id}/r{i + 1}",
                           web_search=vcfg["web_search"])
            verifications[i] = check_verification(risks[i].fact, sanitize_verification(vr.data))
        except LLMUnavailable:
            pass  # одно утверждение не проверили — оно остаётся с паспортом А и пометкой

    out = []
    for i, rk in enumerate(risks):
        v = verifications.get(i)
        final = finalize(rk, v, devil_cites, cap)
        if i in top and v is None:
            final = final.model_copy(update={"method": Method.needs_human, "confidence": min(final.confidence, cap),
                                             "note": "in top_k but not verified (LLM outage or isolation leak)"})
        out.append(CheckedRisk(id=f"r{i + 1}", category=rk.category, fact=rk.fact, implication=rk.implication,
                               severity=rk.severity, strength=round(rk.severity * rk.passport.confidence, 2),
                               devil_passport=rk.passport, verification=v, final=final))
    return AgentsResult(model_id=m.id, name=m.name, status="done", risks=out, isolation_ok=isolation_ok)


def run_agents() -> list[AgentsResult]:
    models = {d["id"]: BusinessModel(**d) for d in json.loads((ROOT / "data" / "models.json").read_text(encoding="utf-8"))}
    filtered = [FilterResult(**d) for d in json.loads((ROOT / "data" / "filter_results.json").read_text(encoding="utf-8"))]
    results = [run_for_model(models[f.model_id], f) for f in filtered if f.status == "passed"]
    (ROOT / "data" / "agents_results.json").write_text(
        json.dumps([r.model_dump() for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
    return results
