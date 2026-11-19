"""Собирает reports/demo_report.html на ВЫМЫШЛЕННЫХ данных — чтобы посмотреть вид карточек без API-ключа.
Компании, цифры и ссылки (example.com) придуманы. Реальный отчёт: python run.py all

    python scripts/make_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import scoring  # noqa: E402
from pipeline.agents import AgentsResult, CheckedRisk, VerifierOutput  # noqa: E402
from pipeline.config import ROOT  # noqa: E402
from pipeline.filter import CriterionResult, FilterResult  # noqa: E402
from pipeline.llm import LLMResult  # noqa: E402
from pipeline.render import render  # noqa: E402
from pipeline.schemas import BusinessModel, Claim, Method  # noqa: E402


def C(text, url=None, date=None, method=Method.primary, conf=0.8, **kw):
    return Claim(text=text, source_url=url, source_date=date, method=method, confidence=conf, **kw)


def model(i, name, liner):
    return BusinessModel(id=f"demo-{i}", name=name, one_liner=liner, description="demo", source_url="https://example.com",
                         fetched_at="2026-11-19T00:00:00", industry="B2B", website=f"https://{name.lower()}.example.com")


def passed(m, analog, usd):
    crit = [CriterionResult(criterion_id=c, verdict="pass", passed=True, reason="ok",
                            claim=C("По описанию: " + c, "https://example.com/yc", "2026-11"))
            for c in ("online_no_license", "recurring_revenue", "no_balance_sheet_risk", "not_trading_or_gov")]
    crit.append(CriterionResult(criterion_id="big_analog", verdict="pass", passed=True, reason="ok",
                                claim=C(f"{analog}: ${usd / 1e6:,.0f}M", "https://news.example.com/deal", "2024",
                                        Method.calculation)))
    return FilterResult(model_id=m.id, name=m.name, status="passed", results=crit,
                        analog={"analog_name": analog, "conservative_usd": usd, "same_model_reason": "same"})


def risk(i, fact, sev, status, url, conf=0.8, primary=True):
    v = None if status == "unverified" else VerifierOutput(status=status, found_source_url=url, is_primary=primary,
                                                           evidence="demo", confidence=conf)
    method = {"confirmed": Method.primary if primary else Method.model_judgment, "contradicted": Method.needs_human,
              "partially": Method.needs_human}.get(status, Method.model_judgment)
    final = C(fact, url, "2026", method, conf if status != "contradicted" else 0.2,
              note="contradicted: источник говорит обратное" if status == "contradicted" else "")
    return CheckedRisk(id=f"r{i}", category="competition", fact=fact, implication="(скрыто от Б)", severity=sev,
                       strength=sev * conf, devil_passport=final, verification=v, final=final)


def fx(id_, score, line, url=None, method=Method.primary):
    return scoring.FactorScore(id=id_, score=score, line=line, claim=C(line, url, "2026", method, 0.7))


DEMO = [
    (model(1, "EssayPilot", "AI-черновик проверки эссе для преподавателей"), "BigEdu Corp", 1.75e9,
     [risk(1, "Крупный конкурент уже включил проверку эссе в школьный тариф", 4, "confirmed", "https://rival.example.com/edu"),
      risk(2, "Регулятор ЕС запрещает AI-оценку учеников", 3, "contradicted", "https://law.example.com/ai-act"),
      risk(3, "Центры не доверят AI оценку, которую видят родители", 2, "unverified", None)],
     scoring.ScorerOutput(
         idea="AI делает черновик проверки эссе, преподаватель только правит — минуты вместо часов.",
         why_pay="Владельцы репетиторских центров: тот же преподаватель ведёт больше учеников → больше выручки.",
         revenue="Подписка ~£30–50/мес за преподавателя", analog_growth="стал стандартом проверки в университетах",
         first_client="Частные центры подготовки к экзаменам, UK, 5–20 преподавателей",
         channel="холодные письма + бесплатная проверка 10 эссе",
         stages=[scoring.Stage(name="тест", weeks_min=2, weeks_max=2), scoring.Stage(name="демо", weeks_min=2, weeks_max=4),
                 scoring.Stage(name="оплата", weeks_min=2, weeks_max=4)],
         factors=[fx("client_access", 5, "центры в открытых каталогах", "https://dir.example.com"),
                  fx("sales_cycle", 4, "решает владелец"), fx("test_speed", 5, "готовая LLM + форма"),
                  fx("demand_evidence", 4, "конкуренты берут деньги", "https://rival.example.com/pricing"),
                  fx("newcomer_room", 2, "тесно: крупные игроки", method=Method.model_judgment)],
         risk_takeaways=[scoring.RiskTakeaway(id="r1", takeaway="не идём в школы, начинаем с частных центров"),
                         scoring.RiskTakeaway(id="r2", takeaway="в UK не действует, старту не мешает"),
                         scoring.RiskTakeaway(id="r3", takeaway="главный вопрос для демо")],
         test_steps=[scoring.TestStep(action="100 писем владельцам центров", success="≥5 демо"),
                     scoring.TestStep(action="Демо на реальных эссе", success="≥70% комментариев без правки"),
                     scoring.TestStep(action="Бесплатная проба 2 недели", success="1 оплата")])),
    (model(2, "LedgerMate", "AI-бухгалтерия для фрилансеров в ЕС"), "BooksCo", 900e6,
     [risk(1, "Налоговые правила различаются в каждой стране ЕС", 4, "confirmed", "https://tax.example.com"),
      risk(2, "Фрилансеры уходят после налогового сезона", 3, "partially", "https://survey.example.com", 0.6)],
     scoring.ScorerOutput(
         idea="Бот сам разносит банковские операции по налоговым категориям и готовит декларацию.",
         why_pay="Фрилансеры в Польше: бухгалтер стоит €80/мес, а здесь €15.", revenue="Подписка €15/мес",
         analog_growth="вырос на малом бизнесе в одной стране, потом расширялся", first_client="IT-фрилансеры B2B в Польше",
         channel="сообщества фрилансеров + контент о налогах",
         stages=[scoring.Stage(name="тест", weeks_min=3, weeks_max=5), scoring.Stage(name="первые пользователи", weeks_min=3, weeks_max=5)],
         factors=[fx("client_access", 4, "живые сообщества"), fx("sales_cycle", 5, "платят картой сами"),
                  fx("test_speed", 2, "нужны банковские интеграции"), fx("demand_evidence", 3, "спрос сезонный"),
                  fx("newcomer_room", 3, "локальные игроки слабые", method=Method.model_judgment)],
         risk_takeaways=[scoring.RiskTakeaway(id="r1", takeaway="стартуем только с Польши"),
                         scoring.RiskTakeaway(id="r2", takeaway="годовой тариф со скидкой")],
         test_steps=[scoring.TestStep(action="Лендинг + 3 поста в сообществах", success="≥50 заявок"),
                     scoring.TestStep(action="Ручная разноска для 10 человек", success="≥3 оплаты")])),
    (model(3, "FleetQuote", "AI-расчёт тарифов для грузоперевозчиков"), "FreightAI", 320e6,
     [risk(1, "Перевозчики заключают контракты на год вперёд", 5, "confirmed", "https://logistics.example.com"),
      risk(2, "Нужны данные о ставках, которых нет в открытом доступе", 4, "not_confirmed", None, 0.4)],
     scoring.ScorerOutput(
         idea="Модель считает ставку на рейс по истории рынка за секунды вместо звонков диспетчеру.",
         why_pay="Средние перевозчики: ошибка в ставке съедает маржу рейса.", revenue="Подписка от $300/мес",
         analog_growth="продавал крупным брокерам", first_client="Перевозчики США, 20–100 грузовиков",
         channel="LinkedIn + отраслевые выставки",
         stages=[scoring.Stage(name="данные и модель", weeks_min=6, weeks_max=8), scoring.Stage(name="продажа", weeks_min=6, weeks_max=10)],
         factors=[fx("client_access", 2, "сложно выйти онлайн", method=Method.model_judgment),
                  fx("sales_cycle", 2, "годовые контракты"), fx("test_speed", 2, "нужны закрытые данные"),
                  fx("demand_evidence", 4, "аналоги продаются"), fx("newcomer_room", 3, "есть ниша среднего сегмента")],
         risk_takeaways=[scoring.RiskTakeaway(id="r1", takeaway="продажа только к сезону контрактов"),
                         scoring.RiskTakeaway(id="r2", takeaway="без данных теста не будет")],
         test_steps=[scoring.TestStep(action="20 интервью с диспетчерами", success="≥5 подтвердят боль")])),
]


def main() -> None:
    cards, filtered = [], []
    for m, analog, usd, risks, out in DEMO:
        f = passed(m, analog, usd)
        scoring.call_json = lambda _out=out, **kw: LLMResult(_out, "demo", "demo", 1)
        cards.append(scoring.build_card(m, f, AgentsResult(model_id=m.id, name=m.name, status="done", risks=risks)))
        filtered.append(f)
    for name, crit, reason in [("RoboJoint", "online_no_license", "Product is physical hardware (robot joints)."),
                               ("PredictX", "not_trading_or_gov", "Prediction market is a form of trading.")]:
        filtered.append(FilterResult(model_id=name, name=name, status="rejected", failed_criteria=[crit],
                                     results=[CriterionResult(criterion_id=crit, verdict="fail", passed=False,
                                                              reason=reason, claim=C(reason))]))
    html = render(scoring.rank(cards), filtered, n_collected=5, demo=True)
    out = ROOT / "reports" / "demo_report.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Демо-отчёт → {out}")
    for c in scoring.rank(cards):
        print(f"  #{c.rank} {c.name}: {c.verdict_label} · {c.weeks} · {c.chance} · {c.internal}")


if __name__ == "__main__":
    main()
