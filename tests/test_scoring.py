"""Расчёты карточки — чистые функции, без LLM."""
from pipeline import scoring as sc
from pipeline.config import load_config
from pipeline.schemas import Claim, Method

CFG = load_config()["scoring"]


def fs(**scores):
    return [sc.FactorScore(id=k, score=v, line="x", claim=Claim(text="t", method=Method.primary, confidence=1))
            for k, v in scores.items()]


ALL = ["client_access", "sales_cycle", "test_speed", "demand_evidence", "newcomer_room"]


def test_base_score_bounds_and_weights():
    assert sc.base_score(fs(**{k: 5 for k in ALL}), CFG) == 100
    assert sc.base_score(fs(**{k: 1 for k in ALL}), CFG) == 20
    # продажи весят больше, чем место для новичка
    a = sc.base_score(fs(**{**{k: 3 for k in ALL}, "sales_cycle": 5}), CFG)
    b = sc.base_score(fs(**{**{k: 3 for k in ALL}, "newcomer_room": 5}), CFG)
    assert a > b


def test_penalty_depends_on_verification():
    assert sc.risk_penalty(5, "confirmed", CFG) == 10
    assert sc.risk_penalty(5, "partially", CFG) == 5
    assert sc.risk_penalty(5, "contradicted", CFG) == 0


def test_weak_sources_widen_weeks_range():
    st = [sc.Stage(name="a", weeks_min=2, weeks_max=4), sc.Stage(name="b", weeks_min=4, weeks_max=6)]
    assert sc.weeks_range(st, 1.0, CFG) == (6, 10)
    lo, hi = sc.weeks_range(st, 0.0, CFG)
    assert lo < 6 and hi > 10


def test_low_reliability_forces_manual_check():
    assert sc.decide(80, 0.9, (6, 10), CFG) == ("launch", "высокий")
    assert sc.decide(80, 0.3, (6, 10), CFG)[0] == "check_manually"
    assert sc.decide(30, 0.3, (6, 10), CFG)[0] == "skip"          # слабую не отправляем «на проверку»
    assert sc.decide(80, 0.9, (14, 20), CFG)[1] == "низкий"       # не успеваем за 3 месяца
    assert sc.decide(80, 0.9, (5, 15), CFG)[1] == "средний"       # в худшем случае не успеваем — ступенью ниже


def test_source_tag():
    t = sc.source_tag(Claim(text="t", source_url="https://www.grammarly.com/edu", source_date="2026-01",
                            method=Method.primary, confidence=0.9))
    assert t.label == "grammarly.com, 2026" and t.mark == "✓"
    assert sc.source_tag(Claim(text="t", method=Method.model_judgment, confidence=0.5)).label == "оценка модели"


def test_fmt_usd():
    assert sc.fmt_usd(1.75e9) == "$1,8 млрд" and sc.fmt_usd(450e6) == "$450 млн" and sc.fmt_usd(2e9) == "$2 млрд"


def test_penalty_uses_scorer_severity_not_devil():
    from pipeline import agents as ag
    from pipeline.filter import FilterResult
    from pipeline.llm import LLMResult
    from pipeline.schemas import BusinessModel

    m = BusinessModel(id="x", name="X", one_liner="o", description="d", source_url="u",
                      fetched_at="2026-11-19T00:00:00", industry="B2B")
    f = FilterResult(model_id="x", name="X", status="passed", analog={"analog_name": "Big", "conservative_usd": 1e9})
    c = Claim(text="t", source_url="https://a.com", method=Method.primary, confidence=1)
    risks = [ag.CheckedRisk(id=f"r{i}", category="competition", fact=f"fact {i}", implication="i", severity=5,
                            strength=5, devil_passport=c, final=c,
                            verification=ag.VerifierOutput(status="confirmed", confidence=1)) for i in (1, 2)]
    a = ag.AgentsResult(model_id="x", name="X", status="done", risks=risks)
    out = sc.ScorerOutput(idea="i", why_pay="w", revenue="r", analog_growth="g", first_client="f", channel="c",
                          stages=[sc.Stage(name="s", weeks_min=2, weeks_max=4)], factors=fs(**{k: 3 for k in ALL}),
                          risk_takeaways=[sc.RiskTakeaway(id="r1", takeaway="t", severity=2),
                                          sc.RiskTakeaway(id="r2", takeaway="t")])   # r2 без оценки → у А, не выше потолка

    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(sc, "call_json", lambda **kw: LLMResult(out, "p", "m", 1))
    try:
        card = sc.build_card(m, f, a)
    finally:
        mp.undo()
    pen = {r.id: r.penalty for r in card.risks}
    assert pen == {"r1": sc.risk_penalty(2, "confirmed", CFG), "r2": sc.risk_penalty(3, "confirmed", CFG)}
    assert card.internal["severity"]["r1"] == {"devil": 5, "scorer": 2}
    # просили 5 рисков, нашлось 2 → 3 недостающих штрафуются
    expected = load_config()["agents"]["devil"]["max_risks"] - 2
    assert card.internal["missing_risks"] == expected
    assert card.internal["risk_penalty"] == min(sum(pen.values()) + expected * CFG["risk_penalty"]["missing_risk_penalty"],
                                                CFG["risk_penalty"]["max_total"])


def test_needs_ru():
    from pipeline.translate import needs_ru
    assert needs_ru("Major cloud providers offer confidential computing") and needs_ru("conservative valuation $80M < $240M")
    assert not needs_ru("Подписка ~$30/мес за преподавателя") and not needs_ru("https://a.com/x") and not needs_ru("Keiki")
    assert needs_ru("Уточнить: «The one who remembers everything, plans effortlessly» — не удалось проверить")


def test_source_tag_downgrades_weak_sources():
    # «первоисточник» без ссылки — оценка модели; ссылка не из поиска — нужен человек
    assert sc.source_tag(Claim(text="t", method=Method.primary, confidence=0.9)).mark == "≈"
    assert sc.source_tag(Claim(text="t", source_url="https://a.com", method=Method.calculation, confidence=0.3)).mark == "?"
    assert sc.source_tag(Claim(text="t", source_url="https://a.com", method=Method.calculation, confidence=0.8,
                               note="source URL not found in web-search results — possibly hallucinated")).mark == "?"


def _assemble(factor_url, analog_usd=1e9, risk_status="confirmed"):
    from pipeline import agents as ag
    from pipeline.filter import CriterionResult, FilterResult
    from pipeline.schemas import BusinessModel

    m = BusinessModel(id="x", name="X", one_liner="o", description="d", source_url="https://yc.com/x",
                      fetched_at="2026-11-19T00:00:00", industry="B2B")
    ac = Claim(text="Big: $1 млрд", source_url="https://big.com", method=Method.calculation, confidence=0.8)
    f = FilterResult(model_id="x", name="X", status="passed", analog={"analog_name": "Big", "conservative_usd": analog_usd},
                     results=[CriterionResult(criterion_id="big_analog", verdict="pass", passed=True, reason="r", claim=ac)])
    c = Claim(text="risk", source_url="https://r.com", method=Method.primary, confidence=1)
    risks = [ag.CheckedRisk(id="r1", category="competition", fact="risk", implication="i", severity=3, strength=3,
                            devil_passport=c, final=c, verification=ag.VerifierOutput(status=risk_status, confidence=1))]
    a = ag.AgentsResult(model_id="x", name="X", status="done", risks=risks)
    fl = fs(**{k: 4 for k in ALL})
    for fx in fl:
        fx.claim = Claim(text="same", source_url=factor_url, method=Method.primary, confidence=0.9)
    out = sc.ScorerOutput(idea="i", why_pay="w", revenue="r", analog_growth="g", first_client="f", channel="c",
                          stages=[sc.Stage(name="s", weeks_min=2, weeks_max=4)], factors=fl,
                          test_steps=[sc.TestStep(action="a", success="b")])
    return sc.assemble_card(m, f, a, out)


def test_invented_scorer_url_is_model_judgment():
    card = _assemble("https://www.statista.com/statistics/1234567/x")
    fac = [p for p in card.passport if p.claim.text == "same"]
    assert len(fac) == 1                                          # одинаковые утверждения — одна строка паспорта
    assert fac[0].mark == "≈" and fac[0].url is None and "statista" in fac[0].claim.note
    assert _assemble("https://yc.com/x/").passport[-2].mark == "✓"   # ссылка из входных данных — остаётся


def test_too_big_analog_and_contradicted_risk():
    card = _assemble(None, analog_usd=250e9, risk_status="contradicted")
    assert card.analog_source.mark == "?" and card.first_steps[0].startswith("Проверить аналог Big")
    assert card.risks == []                                       # опровергнутый риск на карточку не идёт
    assert card.internal["scorer_out"]["idea"] == "i"            # сохранён для пересборки без LLM
