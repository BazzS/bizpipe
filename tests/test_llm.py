"""Проверка политики сбоев без реальных вызовов: провайдеры подменяются."""
import json

import pytest
from pydantic import BaseModel

from pipeline import llm


class Out(BaseModel):
    ok: bool


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "load_config", lambda: {"llm": {
        "chain": [{"provider": "a", "model": "m1", "retries": 2},
                  {"provider": "b", "model": "m2", "retries": 2}],
        "backoff_seconds": [0], "timeout_seconds": 1, "temperature": 0}})


def _call(**kw):
    return llm.call_json(task="t", system="s", user="u", schema=Out, input_fields=["x"], subject_id="id", **kw)


def test_fallback_to_second_provider(monkeypatch):
    def a(*_):
        raise TimeoutError("down")
    monkeypatch.setattr(llm, "PROVIDERS", {"a": a, "b": lambda *_: (json.dumps({"ok": True}), [])})
    r = _call()
    assert r.provider == "b" and r.attempts == 3


def test_invalid_json_counts_as_failure(monkeypatch):
    answers = iter(["not json", '```json\n{"ok": true}\n```'])
    monkeypatch.setattr(llm, "PROVIDERS", {"a": lambda *_: (next(answers), []), "b": None})
    r = _call()
    assert r.provider == "a" and r.attempts == 2


def test_chain_exhausted(monkeypatch):
    def boom(*_):
        raise ConnectionError("x")
    monkeypatch.setattr(llm, "PROVIDERS", {"a": boom, "b": boom})
    with pytest.raises(llm.LLMUnavailable):
        _call()


def test_unconfigured_provider_skipped_without_retries(monkeypatch):
    calls = []
    def a(*_):
        calls.append(1)
        raise llm.ProviderNotConfigured("KEY not set")
    monkeypatch.setattr(llm, "PROVIDERS", {"a": a, "b": lambda *_: (json.dumps({"ok": True}), [])})
    r = _call()
    assert r.provider == "b" and len(calls) == 1


def test_cost_estimate():
    prices = {"web_search_per_call": 0.01, "m": {"input_per_m": 1.0, "output_per_m": 2.0}}
    assert llm.estimate_cost("m", {"input_tokens": 1_000_000, "output_tokens": 500_000, "searches": 3}, prices) == 2.03


def test_budget_profile_overrides_only_its_keys(monkeypatch):
    from pipeline import config
    config.load_config.cache_clear()
    monkeypatch.setenv("BIZPIPE_CONFIG", "config.budget.yaml")
    cfg = config.load_config()
    config.load_config.cache_clear()
    assert cfg["llm"]["chain"][0]["model"] == "gpt-4o-mini" and cfg["llm"]["max_tool_calls"] == 1
    assert cfg["agents"]["verifier"]["top_k"] == 2 and cfg["agents"]["verifier"]["visible_fields"]  # остальное на месте
    assert cfg["scoring"]["factors"]                                                               # нетронутые секции


def test_task_chain_override(monkeypatch):
    monkeypatch.setattr(llm, "load_config", lambda: {"llm": {
        "chain": [{"provider": "a", "model": "m1", "retries": 1}],
        "task_chains": {"t": [{"provider": "b", "model": "strong", "retries": 1}]},
        "backoff_seconds": [0], "timeout_seconds": 1, "temperature": 0}})
    monkeypatch.setattr(llm, "PROVIDERS", {"a": None, "b": lambda *_: (json.dumps({"ok": True}), [])})
    assert _call().model == "strong"
