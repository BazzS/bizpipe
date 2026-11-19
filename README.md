# Конвейер проверки бизнес-моделей

Прототип: собирает бизнес-модели из открытого источника, отсеивает их по критериям,
прогоняет через двух независимых агентов и строит рейтинг по вероятности получить первую оплату за 3 месяца.

## Результат

**Отчёт: https://bazzs.github.io/bizpipe/** (рейтинг и карточки, [демо на вымышленных данных](https://bazzs.github.io/bizpipe/reports/demo_report.html)).

Прогон 19.11.2026 на реальном API (gpt-4o-mini, оценщик — gpt-4o), $0.28: собрано 15 моделей, прошли 4, отсеяно 11.
Рейтинг: Keiki, Text.ai, Contrario — «Запускать тест»; Tinfoil — «Отложить».
Почему отсеяны — в отчёте и в `logs/20261119T140959-af937c/rejected.jsonl`; изоляция агентов — в `agents.jsonl` там же.

## Запуск

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # впиши OPENAI_API_KEY
export BIZPIPE_CONFIG=config.budget.yaml   # дешёвый профиль (как в прогоне выше)
python run.py collect       # шаг 1: сбор моделей → data/models.json
python run.py filter        # шаг 2: критерии → data/filter_results.json, logs/<run>/rejected.jsonl
python run.py agents        # шаг 3: агенты А и Б → data/agents_results.json, logs/<run>/agents.jsonl
python run.py score         # шаг 4: оценщик → data/cards.json
python run.py report        # → reports/report.html
python run.py all           # всё подряд
python scripts/make_demo.py # вид отчёта на вымышленных данных, без ключа
pytest -q
```

Все параметры — в `config.yaml`. Журналы каждого запуска — в `logs/<run_id>/*.jsonl`.

## Статус

- [x] Шаг 1 — сбор (YC Directory), слой LLM с ретраями и запасным провайдером
- [x] Шаг 2 — фильтр по критериям (критерии и пороги в config.yaml, промпты в prompts/)
- [x] Шаг 3 — два изолированных агента (изоляция: белый список полей + аудит промпта, см. agents.py)
- [x] Шаг 4 — оценщик, карточки, рейтинг, HTML-отчёт (веса и пороги в config.yaml → scoring)

## Про историю коммитов

Прототип писался локально без git; репозиторий создан в конце, поэтому первый коммит большой.
Как шла работа (прогоны, что находили и что правили после каждого), записано в `DECISIONS.md` и `KNOWN_ISSUES.md`;
дальнейшие правки — отдельными коммитами.

## Документы

- `DECISIONS.md` — пять развилок и предложения по постановке задачи
- `KNOWN_ISSUES.md` — что не работает или работает криво
- `VIDEO_SCRIPT.md` — сценарий видео на 3 минуты
