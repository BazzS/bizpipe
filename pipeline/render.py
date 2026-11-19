"""HTML-отчёт: рейтинг + карточки + отсеянные. Одна самодостаточная страница reports/report.html."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT, load_config
from .filter import FilterResult
from .scoring import Card
from .translate import translate_all

METHOD_RU = {"primary": "первоисточник", "calculation": "расчёт", "model_judgment": "суждение модели",
             "needs_human": "нужен человек"}


def passport_tip(tag, tr: dict[str, str] | None = None) -> str:
    c, tr = tag.claim, tr or {}
    parts = [f"метод: {METHOD_RU[c.method.value]}", f"уверенность: {c.confidence:.1f}"]
    if c.source_date:
        parts.append(f"дата: {c.source_date}")
    if c.note:
        parts.append(tr.get(c.note, c.note))
    if c.conflicting_values:
        parts.append("версии: " + " | ".join(c.conflicting_values))
    return " · ".join(parts)


def display_texts(cards: list[Card], filtered: list[FilterResult]) -> list[str]:
    """Всё, что отчёт показывает дословно из ответов агентов, — кандидаты на перевод."""
    out = []
    for c in cards:
        out += [c.one_liner, *c.first_steps, *(r.fact for r in c.risks)]
        out += [x for p in c.passport for x in (p.claim.text, p.claim.note)]
    for f in filtered:
        out += [x.reason for x in f.results if not x.passed]
    return [t for t in out if t]


def render(cards: list[Card], filtered: list[FilterResult], n_collected: int, demo: bool = False,
           tr: dict[str, str] | None = None) -> str:
    cfg = load_config()
    crit_labels = {c["id"]: c.get("label", c["id"]) for c in cfg["filter"]["criteria"]}
    rejected = []
    for f in filtered:
        if f.status == "rejected":
            first = next(x for x in f.results if not x.passed)
            rejected.append({"name": f.name, "criterion": crit_labels.get(first.criterion_id, first.criterion_id),
                             "reason": first.reason, "all": [crit_labels.get(x, x) for x in f.failed_criteria]})
    not_verified = [f for f in filtered if f.status == "not_verified"] + [c for c in cards if c.verdict == "not_verified"]

    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())
    tr = tr or {}
    env.filters["tip"] = lambda tag: passport_tip(tag, tr)
    env.filters["ru"] = lambda t: tr.get(t, t) if t else t
    env.filters["method"] = lambda m: METHOD_RU.get(getattr(m, "value", m), m)
    ranked = [c for c in cards if c.rank]
    return env.get_template("report.html").render(
        ranked_weak_first=sorted(ranked, key=lambda c: -c.rank),   # как в ТЗ: от слабой к перспективной
        rejected=rejected, not_verified=not_verified,
        n_collected=n_collected, n_passed=sum(f.status == "passed" for f in filtered),
        n_rejected=len(rejected), factors=cfg["scoring"]["factors"], demo=demo,
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


def run_render(demo: bool = False) -> str:
    load = lambda name: json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
    cards = [Card(**d) for d in load("cards.json")]
    filtered = [FilterResult(**d) for d in load("filter_results.json")]
    tr = translate_all(display_texts(cards, filtered))
    html = render(cards, filtered, n_collected=len(load("models.json")), demo=demo, tr=tr)
    out = ROOT / "reports" / "report.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)
