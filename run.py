"""Точка входа.

    python run.py collect [--refresh]   # шаг 1: сбор
    python run.py filter                # шаг 2: фильтр по критериям
    python run.py agents                # шаг 3: агент А + агент Б
    python run.py score                 # шаг 4: оценщик → карточки → data/cards.json
    python run.py cards                 # пересобрать карточки из сохранённого ответа оценщика, без LLM
    python run.py report                # HTML-отчёт → reports/report.html
    python run.py all                   # всё по порядку
"""
from __future__ import annotations

import argparse

from pipeline.agents import run_agents
from pipeline.collect import collect
from pipeline.filter import run_filter
from pipeline.llm import COST
from pipeline.logger import LOG_DIR
from pipeline.render import run_render
from pipeline.scoring import run_cards, run_scoring


def main() -> None:
    p = argparse.ArgumentParser(description="Конвейер проверки бизнес-моделей")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="собрать бизнес-модели из источника")
    c.add_argument("--refresh", action="store_true", help="скачать датасет заново, игнорируя кеш")
    sub.add_parser("filter", help="прогнать модели через критерии отбора")
    sub.add_parser("agents", help="агент А ищет риски, агент Б проверяет источники")
    sub.add_parser("score", help="оценщик + карточки + рейтинг")
    sub.add_parser("cards", help="пересобрать карточки без LLM (после правки правил в config.yaml)")
    sub.add_parser("report", help="HTML-отчёт из data/cards.json")
    a = sub.add_parser("all", help="collect → filter → agents → score → report")
    a.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    cmds = ["collect", "filter", "agents", "score", "report"] if args.cmd == "all" else [args.cmd]
    for cmd in cmds:
        print(f"== {cmd}")
        step(cmd, args)
    if COST["calls"]:
        print(f"Стоимость (оценка): ${COST['usd']:.2f} · вызовов LLM: {COST['calls']} · веб-поисков: {COST['searches']}")
    print(f"Журнал: {LOG_DIR}")


def step(cmd: str, args) -> None:
    if cmd == "collect":
        models = collect(refresh=getattr(args, "refresh", False))
        print(f"Собрано моделей: {len(models)} → data/models.json")
        for m in models:
            print(f"  [{m.industry:10}] {m.name}: {m.one_liner}")
    elif cmd == "filter":
        res = run_filter()
        by = {s: [r for r in res if r.status == s] for s in ("passed", "rejected", "not_verified")}
        print(f"Прошли: {len(by['passed'])}  Отсеяны: {len(by['rejected'])}  Не проверены (сбой LLM): {len(by['not_verified'])}")
        for r in by["passed"]:
            flags = [x.criterion_id for x in r.results if x.flagged]
            print(f"  ✓ {r.name}" + (f"  [нужен человек: {', '.join(flags)}]" if flags else ""))
        for r in by["rejected"]:
            first = next(x for x in r.results if not x.passed)
            print(f"  ✗ {r.name}: {first.criterion_id} — {first.reason}")
        for r in by["not_verified"]:
            print(f"  ? {r.name}: {r.error}")
        print("Результаты → data/filter_results.json")
    elif cmd == "agents":
        for r in run_agents():
            if r.status != "done":
                print(f"  ? {r.name}: {r.error}")
                continue
            print(f"  {r.name}  (изоляция: {'ok' if r.isolation_ok else 'НАРУШЕНА'})")
            for k in r.risks:
                st = k.verification.status if k.verification else "—"
                print(f"    {k.id} sev={k.severity} [{st:18}] {k.fact[:90]}")
        print("Результаты → data/agents_results.json")
    elif cmd in ("score", "cards"):
        for c in (run_scoring() if cmd == "score" else run_cards()):
            w = f"{c.weeks[0]}–{c.weeks[1]} нед." if c.weeks else "—"
            brief = {k: v for k, v in c.internal.items() if k not in ("scorer_out", "severity")}
            print(f"  #{c.rank or '-'} {c.name}: {c.verdict_label} · {w} · шанс {c.chance}  {brief}")
        print("Карточки → data/cards.json")
    elif cmd == "report":
        print(f"Отчёт → {run_render()}")


if __name__ == "__main__":
    main()
