"""Агенты без реальных вызовов: проверяем изоляцию и правила итогового паспорта."""
import json

from pipeline import agents as ag
from pipeline.filter import FilterResult
from pipeline.llm import LLMResult
from pipeline.schemas import BusinessModel, Claim, Method

VISIBLE = ["fact", "source_url", "source_date"]
M = BusinessModel(id="x", name="X", one_liner="o", description="d", source_url="u",
                  fetched_at="2026-11-17T00:00:00", industry="B2B")
F = FilterResult(model_id="x", name="X", status="passed", analog={"analog_name": "Big", "conservative_usd": 1e9})


def risk(fact, sev=4, conf=0.8, url="https://src.com/a", impl="SECRET-IMPLICATION " + "z" * 50):
    return ag.Risk(category="competition", fact=fact, implication=impl, severity=sev,
                   passport=Claim(text=fact, source_url=url, method=Method.primary, confidence=conf))


def test_projection_hides_devil_conclusions():
    r = risk("Grammarly is free for teachers")
    payload = ag.project_for_verifier(r, VISIBLE)
    prompt = ag.verifier_prompt(payload)
    assert set(payload) == set(VISIBLE)
    assert "SECRET-IMPLICATION" not in prompt and "competition" not in prompt and "0.8" not in prompt
    assert ag.audit_isolation(payload, prompt, r, [r], VISIBLE) == []


def test_audit_catches_leaks():
    r1, r2 = risk("fact one " + "a" * 60), risk("fact two " + "b" * 60)
    bad = f"{r1.fact}\n{r2.fact}\n{r1.implication}"
    leaks = ag.audit_isolation({"fact": 1, "severity": 4}, bad, r1, [r1, r2], VISIBLE)
    assert {"field:severity", "implication", "other_claim"} <= set(leaks)


def v(status, conf=0.9, primary=True):
    return ag.VerifierOutput(status=status, found_source_url="https://primary.com", is_primary=primary,
                             evidence="e", confidence=conf)


def test_finalize_rules():
    r = risk("f")
    assert ag.finalize(r, v("confirmed"), [], 0.5).method == Method.primary
    assert ag.finalize(r, v("confirmed", primary=False), [], 0.5).method == Method.model_judgment
    p = ag.finalize(r, v("partially"), [], 0.5)
    assert p.method == Method.needs_human and p.confidence <= 0.5 and p.conflicting_values
    c = ag.finalize(r, v("contradicted", conf=0.9), [], 0.5)
    assert c.method == Method.needs_human and c.confidence == 0.1
    assert ag.finalize(r, v("not_confirmed"), [], 0.5).method == Method.model_judgment


def test_unverified_primary_without_citation_downgraded():
    p = ag.finalize(risk("f", url="https://made-up.com"), None, ["https://real.com"], 0.5)
    assert p.method == Method.model_judgment and p.confidence <= 0.5


def test_run_verifies_only_top_k_one_call_per_claim(monkeypatch):
    risks = [risk("weak " + "a" * 60, sev=1, url="https://src.com/a"), risk("strong " + "b" * 60, sev=5, url="https://src.com/b"),
             risk("mid " + "c" * 60, sev=3, url="https://src.com/c")]
    seen = []

    def fake(**kw):
        if kw["task"] == "agent.devil":
            return LLMResult(ag.DevilOutput(risks=risks), "p", "m", 1, [{"url": "https://src.com/a"}])
        seen.append(kw["user"])
        return LLMResult(v("confirmed"), "p", "m", 1)

    monkeypatch.setattr(ag, "call_json", fake)
    monkeypatch.setattr(ag, "load_config", lambda: {"agents": {
        "devil": {"max_risks": 5, "web_search": True, "categories": ["competition"]},
        "verifier": {"top_k": 2, "web_search": True, "visible_fields": VISIBLE},
        "unverified_confidence_cap": 0.5}})
    res = ag.run_for_model(M, F)
    assert len(seen) == 2 and all("SECRET-IMPLICATION" not in u for u in seen)
    assert all(u.count("Statement:") == 1 for u in seen)                     # одно утверждение на вызов
    verified = {k.id for k in res.risks if k.verification}
    assert verified == {"r2", "r3"} and res.isolation_ok


def test_strip_citations_moves_link_out_of_text():
    from pipeline.schemas import clean_url, strip_citations
    text, urls = strip_citations("Mercor raised $350M ([mercor.com](https://www.mercor.com/news/?utm_source=openai)), at $10B.")
    assert text == "Mercor raised $350M, at $10B." and urls == ["https://www.mercor.com/news/"]
    assert clean_url("([t.com](https://t.com/x?id=3&utm_source=openai&trk=a))") == "https://t.com/x?id=3"


def test_sanitize_risk_fills_missing_source_from_fact():
    r = risk("Google offers X ([google.com](https://cloud.google.com/x?utm_source=openai))", url=None)
    s = ag.sanitize_risk(r)
    assert s.fact == "Google offers X" and s.passport.text == s.fact
    assert s.passport.source_url == "https://cloud.google.com/x"


def test_unverified_never_counts_as_primary():
    p = ag.finalize(risk("f", url="https://src.com/a", conf=0.9), None, ["https://src.com/a"], 0.5)
    assert p.method == Method.model_judgment and p.confidence == 0.5 and p.source_url == "https://src.com/a"


def test_dedupe_drops_repeats_and_same_source():
    a = risk("NVIDIA bundles confidential computing", url="https://nvidia.com/cc")
    b = risk("NVIDIA bundles confidential computing.", url="https://other.com/x")
    c = risk("Another fact from the same page", url="https://www.nvidia.com/cc/")
    d = risk("Independent fact", url=None)
    kept, dropped = ag.dedupe_risks([a, b, c, d])
    assert [k.fact for k in kept] == [a.fact, d.fact] and len(dropped) == 2


def test_confirmed_with_other_numbers_becomes_partially():
    fact = "Mercer has a market cap of about $14.14 billion as of November 19, 2026"
    bad = ag.check_verification(fact, ag.VerifierOutput(status="confirmed", evidence="cap is $14.14 million as of November 18, 2026", confidence=0.9))
    assert bad.status == "partially"
    ok = ag.check_verification("Mercor raised $350 million at a $10 billion valuation in October 2025",
                               ag.VerifierOutput(status="confirmed", evidence="raised $350M at a $10B valuation", confidence=0.9))
    assert ok.status == "confirmed"


def test_hollow_fact_dropped_and_ranges_match():
    hollow = risk("Market will reach US$ million by 2029, at a CAGR of % during 2024-2029", url="https://x.com/r")
    kept, dropped = ag.dedupe_risks([hollow, risk("Real fact", url="https://y.com")])
    assert [k.fact for k in kept] == ["Real fact"] and dropped
    assert ag.numbers_disagree("GoPerfect charges $250–$300 per open role", "charges $300 per open position") == []
    assert ag.numbers_disagree("charges $250–$300 per role", "charges $900 per role") != []
