"""Шаг 4: оценщик + сборка карточки.

LLM («оценщик») пишет только содержимое: идея, кому платить, этапы, баллы факторов по шкале.
Всё числовое считает код по config.scoring: оценку, штраф за риски, надёжность, диапазон срока,
вердикт. Поэтому веса меняются в конфиге без правки кода и без новых вызовов LLM.

Оценщик видит проверенные факты рисков и их статус от агента Б, но НЕ видит выводов и
серьёзности агента А — чтобы не наследовать его пессимизм. Тяжесть риска для штрафа ставит оценщик
(сильная модель, по шкале с якорями); оценка агента А — запасная, если оценщик её не дал.
Причина: агент А на дешёвой модели ставит тяжесть по порядку списка (всегда 5/4/3), и штраф переставал различать модели.
"""
from __future__ import annotations

import json
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .agents import AgentsResult
from .config import ROOT, load_config
from .filter import FilterResult
from .llm import LLMUnavailable, call_json
from .logger import log_event
from .schemas import BusinessModel, Claim, Method, fmt_usd


# ------------------------------------------------------------ что пишет LLM

class Stage(BaseModel):
    name: str
    weeks_min: float = Field(ge=0)
    weeks_max: float = Field(ge=0)


class FactorScore(BaseModel):
    id: str
    score: int = Field(ge=1, le=5)
    line: str
    claim: Claim


class RiskTakeaway(BaseModel):
    id: str
    takeaway: str
    severity: int | None = Field(default=None, ge=1, le=5)   # тяжесть по шкале — ставит оценщик, а не агент А


class TestStep(BaseModel):
    action: str
    success: str


class ScorerOutput(BaseModel):
    idea: str
    why_pay: str
    revenue: str
    analog_growth: str
    first_client: str
    channel: str
    stages: list[Stage]
    factors: list[FactorScore]
    risk_takeaways: list[RiskTakeaway] = []
    test_steps: list[TestStep] = []


# ------------------------------------------------------------ карточка

class SourceTag(BaseModel):
    """Метка у факта: «grammarly.com, 2026 ✓». Полный паспорт — в tooltip и в таблице."""
    label: str
    mark: Literal["✓", "≈", "?"]
    url: str | None = None
    claim: Claim


class CardFactor(BaseModel):
    id: str
    label: str
    line: str
    mark: Literal["✔", "●", "✖"]
    score: int


class CardRisk(BaseModel):
    id: str
    level: str               # Критично / Серьёзно / Терпимо
    status: str              # подтверждено / опровергнуто / ...
    fact: str
    takeaway: str
    source: SourceTag
    penalty: float


class Card(BaseModel):
    model_id: str
    name: str
    one_liner: str
    website: str | None
    verdict: Literal["launch", "park", "skip", "check_manually", "not_verified"]
    verdict_label: str
    chance: str                      # высокий / средний / низкий
    weeks: tuple[int, int] | None
    rank: int | None = None
    # содержимое
    idea: str = ""
    why_pay: str = ""
    revenue: str = ""
    analog: str = ""
    analog_source: SourceTag | None = None
    analog_growth: str = ""
    first_client: str = ""
    channel: str = ""
    stages: list[Stage] = []
    factors: list[CardFactor] = []
    risks: list[CardRisk] = []
    first_steps: list[str] = []
    passport: list[SourceTag] = []
    passport_counts: dict[str, int] = {}
    # внутреннее — для сортировки и аудита, собственнику не показывается
    internal: dict = {}
    error: str | None = None


VERDICT_LABELS = {"launch": "Запускать тест", "park": "Отложить", "skip": "Не брать",
                  "check_manually": "Сначала проверить вручную", "not_verified": "Не проверено (сбой LLM)"}
STATUS_LABELS = {"confirmed": "подтверждено", "partially": "подтверждено частично", "contradicted": "опровергнуто",
                 "not_confirmed": "не подтвердилось", "source_unavailable": "источник недоступен",
                 "unverified": "не проверялось"}


# ------------------------------------------------------------ чистые функции (тестируются)

def source_tag(c: Claim) -> SourceTag:
    mark = {"primary": "✓", "calculation": "✓", "model_judgment": "≈", "needs_human": "?"}[c.method.value]
    host = urlparse(c.source_url).netloc.removeprefix("www.") if c.source_url else ""
    year = (c.source_date or "")[:4]
    label = f"{host}, {year}" if host and year else host or ("оценка модели" if c.method == Method.model_judgment else "без источника")
    return SourceTag(label=label, mark=mark, url=c.source_url, claim=c)


def base_score(factors: list[FactorScore], cfg: dict) -> float:
    by_id = {f.id: f.score for f in factors}
    total = sum(spec["weight"] * by_id.get(fid, 1) for fid, spec in cfg["factors"].items())
    weight_sum = sum(spec["weight"] for spec in cfg["factors"].values())
    return total / weight_sum * 20  # 1..5 → 20..100


def risk_penalty(severity: int, status: str, cfg: dict) -> float:
    rp = cfg["risk_penalty"]
    return severity * rp["per_severity"] * rp["status_factor"].get(status, 0.25)


def reliability(claims: list[Claim], cfg: dict) -> float:
    if not claims:
        return 0.0
    w = cfg["method_reliability"]
    return sum(w[c.method.value] for c in claims) / len(claims)


def weeks_range(stages: list[Stage], rel: float, cfg: dict) -> tuple[int, int]:
    lo = sum(s.weeks_min for s in stages)
    hi = sum(max(s.weeks_min, s.weeks_max) for s in stages)
    k = (1 - rel) * cfg["weeks_widening"]        # слабые источники → шире диапазон
    lo, hi = lo * (1 - k), hi * (1 + k)
    return max(1, round(lo)), max(1, round(hi))


def decide(score: float, rel: float, weeks: tuple[int, int], cfg: dict) -> tuple[str, str]:
    lv = cfg["chance_levels"]
    chance = "высокий" if score >= lv["high"] else "средний" if score >= lv["medium"] else "низкий"
    if weeks[0] > cfg["max_weeks"]:              # даже в лучшем случае не успеваем за 3 месяца
        chance = "низкий"
    elif weeks[1] > cfg["max_weeks"]:            # в худшем случае не успеваем → на ступень ниже
        chance = {"высокий": "средний", "средний": "низкий"}.get(chance, chance)
    v = cfg["verdicts"]
    verdict = "launch" if score >= v["launch"] else "park" if score >= v["park"] else "skip"
    if verdict != "skip" and rel < cfg["min_reliability"]:
        verdict = "check_manually"
    return verdict, chance


def factor_mark(score: int, cfg: dict) -> str:
    m = cfg["factor_marks"]
    return "✔" if score >= m["good"] else "✖" if score <= m["bad"] else "●"


# ------------------------------------------------------------ сборка

def _scorer_input(m: BusinessModel, f: FilterResult, a: AgentsResult) -> str:
    an = f.analog or {}
    risks = "\n".join(
        f"- {k.id} [{k.category}] {k.fact} — check: {k.verification.status if k.verification else 'unverified'}"
        + (f" ({k.verification.evidence})" if k.verification and k.verification.evidence else "")
        for k in a.risks)
    return (f"Business model: {m.name} — {m.one_liner}\n{m.description}\nWebsite: {m.website}\n"
            f"Largest analog: {an.get('analog_name')} ~${(an.get('conservative_usd') or 0) / 1e6:,.0f}M "
            f"({an.get('same_model_reason', '')})\n\nChecked risk facts:\n{risks or '- none'}")


def build_card(m: BusinessModel, f: FilterResult, a: AgentsResult) -> Card:
    full = load_config()
    cfg = full["scoring"]
    base = dict(model_id=m.id, name=m.name, one_liner=m.one_liner, website=m.website)

    if a.status != "done":
        return Card(**base, verdict="not_verified", verdict_label=VERDICT_LABELS["not_verified"],
                    chance="—", weeks=None, error=a.error)

    factors_txt = "\n".join(f"- {fid}: " + "; ".join(f"{k}={v}" for k, v in spec["rubric"].items())
                            for fid, spec in cfg["factors"].items())
    system = ((ROOT / "prompts" / "scorer.md").read_text()
              .replace("{factors}", factors_txt).replace("{max_steps}", str(cfg["max_test_steps"])))
    try:
        r = call_json(task="score", system=system, user=_scorer_input(m, f, a), schema=ScorerOutput,
                      input_fields=["name", "one_liner", "description", "website", "analog",
                                    "risk.fact", "risk.category", "risk.verification"],
                      subject_id=m.id)
    except LLMUnavailable as e:
        return Card(**base, verdict="not_verified", verdict_label=VERDICT_LABELS["not_verified"],
                    chance="—", weeks=None, error=str(e))
    s: ScorerOutput = r.data

    # --- риски: штраф по статусу проверки, на карточку — самые тяжёлые
    takeaways = {t.id: t for t in s.risk_takeaways}
    card_risks, severities = [], {}
    for k in a.risks:
        status = k.verification.status if k.verification else "unverified"
        t = takeaways.get(k.id)
        # оценщик пропустил риск → тяжесть А, но не выше потолка: А на дешёвой модели завышает
        sev = t.severity if t and t.severity else min(k.severity, cfg["risk_penalty"]["fallback_severity_cap"])
        severities[k.id] = {"devil": k.severity, "scorer": t.severity if t else None}
        card_risks.append(CardRisk(
            id=k.id, level=cfg["severity_labels"][sev], status=STATUS_LABELS[status],
            fact=k.fact, takeaway=t.takeaway if t else "", source=source_tag(k.final),
            penalty=round(risk_penalty(sev, status, cfg), 1)))
    expected = full["agents"]["devil"]["max_risks"]
    missing = max(0, expected - len(card_risks))
    penalty = min(sum(x.penalty for x in card_risks) + missing * cfg["risk_penalty"]["missing_risk_penalty"],
                  cfg["risk_penalty"]["max_total"])
    card_risks.sort(key=lambda x: (-x.penalty, x.id))

    # --- все утверждения карточки → паспорт и надёжность
    analog_claim = next((x.claim for x in f.results if x.criterion_id == "big_analog"), None)
    claims = [x.claim for x in f.results] + [fx.claim for fx in s.factors] + [k.final for k in a.risks]
    # надёжность — только по утверждениям, на которых стоит решение (критерии 1–4 взяты из описания
    # самой компании и почти всегда «первоисточник» — они бы её завышали)
    decision_claims = ([analog_claim] if analog_claim else []) + [fx.claim for fx in s.factors] + [k.final for k in a.risks]
    rel = reliability(decision_claims, cfg)

    score = max(0.0, min(100.0, base_score(s.factors, cfg) - penalty))
    weeks = weeks_range(s.stages, rel, cfg)
    verdict, chance = decide(score, rel, weeks, cfg)

    # --- первые шаги: сначала то, что должен проверить человек, затем план теста
    human = [c for c in claims if c.method == Method.needs_human]
    steps = [f"Уточнить: «{c.text}» — " + ("источники расходятся" if c.conflicting_values else "не удалось проверить")
             for c in human[:2]]
    steps += [f"{t.action} → {t.success}" for t in s.test_steps[: cfg["max_test_steps"]]]

    labels = {fid: spec["label"] for fid, spec in cfg["factors"].items()}
    passport = [source_tag(c) for c in claims]
    an = f.analog or {}
    card = Card(
        **base, verdict=verdict, verdict_label=VERDICT_LABELS[verdict], chance=chance, weeks=weeks,
        idea=s.idea, why_pay=s.why_pay, revenue=s.revenue,
        analog=f"{an.get('analog_name') or '—'} ~{fmt_usd(an.get('conservative_usd'))}",
        analog_source=source_tag(analog_claim) if analog_claim else None, analog_growth=s.analog_growth,
        first_client=s.first_client, channel=s.channel, stages=s.stages,
        factors=[CardFactor(id=fx.id, label=labels.get(fx.id, fx.id), line=fx.line,
                            mark=factor_mark(fx.score, cfg), score=fx.score) for fx in s.factors if fx.id in labels],
        risks=card_risks[: cfg["max_risks_on_card"]],
        first_steps=steps,
        passport=passport,
        passport_counts={m_: sum(1 for p in passport if p.mark == m_) for m_ in ("✓", "≈", "?")},
        internal={"score": round(score, 1), "base": round(base_score(s.factors, cfg), 1),
                  "risk_penalty": round(penalty, 1), "missing_risks": missing, "reliability": round(rel, 2),
                  "severity": severities},
    )
    log_event("score", subject_id=m.id, **card.internal, verdict=verdict, weeks=weeks)
    return card


def rank(cards: list[Card]) -> list[Card]:
    """Ранг 1 = самая перспективная. Непроверенные — без ранга, в конце."""
    scored = sorted([c for c in cards if c.verdict != "not_verified"], key=lambda c: -c.internal["score"])
    for i, c in enumerate(scored, 1):
        c.rank = i
    return scored + [c for c in cards if c.verdict == "not_verified"]


def run_scoring() -> list[Card]:
    load = lambda name: json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
    models = {d["id"]: BusinessModel(**d) for d in load("models.json")}
    filtered = {d["model_id"]: FilterResult(**d) for d in load("filter_results.json")}
    agents = [AgentsResult(**d) for d in load("agents_results.json")]
    cards = rank([build_card(models[a.model_id], filtered[a.model_id], a) for a in agents])
    (ROOT / "data" / "cards.json").write_text(
        json.dumps([c.model_dump() for c in cards], ensure_ascii=False, indent=2), encoding="utf-8")
    return cards
