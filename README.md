# Конвейер проверки бизнес-моделей

Прототип: собирает бизнес-модели из открытого источника, отсеивает их по критериям,
прогоняет через двух независимых агентов и строит рейтинг по вероятности получить первую оплату за 3 месяца.

## Запуск

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # впиши OPENAI_API_KEY
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

## Документы

- `DECISIONS.md` — пять развилок и предложения по постановке задачи
- `KNOWN_ISSUES.md` — что не работает или работает криво
- `VIDEO_SCRIPT.md` — сценарий видео на 3 минуты
