"""Журнал событий в JSONL: одна строка — одно событие. Каждый запуск — свой run_id."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
LOG_DIR = ROOT / "logs" / RUN_ID


def log_event(stream: str, **fields) -> None:
    """stream — имя файла журнала: llm, collect, filter, agents, ..."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), "run_id": RUN_ID, **fields}
    with open(LOG_DIR / f"{stream}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
