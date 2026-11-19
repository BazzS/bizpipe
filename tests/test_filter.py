"""Фильтр без реальных вызовов: call_json подменяется."""
import pytest

from pipeline import filter as flt
from pipeline.llm import LLMResult, LLMUnavailable
from pipeline.schemas import BusinessModel, Claim, Method

CFG = {"min_valuation_usd": 300e6, "tolerance": 0.8, "conflict_ratio": 2.0}
M = BusinessModel(id="x", name="X", one_liner="o", description="d", source_url="u",
                  fetched_at="2026-11-17T00:00:00", industry="B2B")


def _analog(*vals):
    return flt.AnalogOutput(analog_name="Big", values=[
        flt.ValuationPoint(value_usd=v, kind="last_round", source_url=u) for v, u in vals])


def test_analog_pass_no_conflict():
    v, _, claim, info = flt.judge_analog(_analog((1e9, "https://a.com/x"), (1.2e9, "https://b.com/y")),
                                         [{"url": "https://a.com/x"}, {"url": "https://www.b.com/y/"}], CFG)
    assert v == "pass" and claim.method == Method.calculation and not info["conflict"]


def test_analog_conflict_uses_conservative_value():
    v, _, claim, info = flt.judge_analog(_analog((200e6, "https://a.com"), (2e9, "https://b.com")),
                                         [{"url": "https://a.com"}, {"url": "https://b.com"}], CFG)
    assert v == "fail" and info["conservative_usd"] == 200e6
    assert claim.method == Method.needs_human and claim.confidence <= 0.5 and len(claim.conflicting_values) == 2


def test_hallucinated_url_ignored_when_others_cited():
    v, _, _, info = flt.judge_analog(_analog((50e6, "https://fake.com"), (1e9, "https://real.com")),
                                     [{"url": "https://real.com"}], CFG)
    assert v == "pass" and info["conservative_usd"] == 1e9


def test_no_analog_is_uncertain():
    v, *_ = flt.judge_analog(flt.AnalogOutput(analog_name=None), [], CFG)
    assert v == "uncertain"


def _verdicts(**kv):
    ev = Claim(text="t", method=Method.primary, confidence=0.9)
    return flt.ScreenOutput(verdicts=[flt.LLMCriterionVerdict(criterion_id=k, verdict=v, reason="r", evidence=ev)
                                      for k, v in kv.items()])


ALL_PASS = dict(online_no_license="pass", recurring_revenue="pass", no_balance_sheet_risk="pass",
                not_trading_or_gov="pass")


def _fake(screen, analog=None, analog_cites=()):
    calls = []

    def fake(**kw):
        calls.append(kw["task"])
        if kw["task"] == "filter.screen":
            return LLMResult(screen, "p", "m", 1)
        if analog is None:
            raise LLMUnavailable("down")
        return LLMResult(analog, "p", "m", 1, [{"url": u} for u in analog_cites])
    return fake, calls


def test_reject_short_circuits_web_search(monkeypatch):
    fake, calls = _fake(_verdicts(**{**ALL_PASS, "online_no_license": "fail"}))
    monkeypatch.setattr(flt, "call_json", fake)
    r = flt.screen_model(M)
    assert r.status == "rejected" and r.failed_criteria == ["online_no_license"] and calls == ["filter.screen"]


def test_uncertain_passes_flagged(monkeypatch):
    fake, _ = _fake(_verdicts(**{**ALL_PASS, "recurring_revenue": "uncertain"}),
                    _analog((1e9, "https://a.com")), ["https://a.com"])
    monkeypatch.setattr(flt, "call_json", fake)
    r = flt.screen_model(M)
    rr = next(x for x in r.results if x.criterion_id == "recurring_revenue")
    assert r.status == "passed" and rr.flagged and rr.claim.method == Method.needs_human


def test_llm_outage_is_not_verified_not_rejected(monkeypatch):
    fake, _ = _fake(_verdicts(**ALL_PASS), analog=None)
    monkeypatch.setattr(flt, "call_json", fake)
    assert flt.screen_model(M).status == "not_verified"
