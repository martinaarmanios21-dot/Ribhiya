"""Per-business monthly AI cap: bounds what one business costs to run, refunds partial reservations, and one
business running out never pauses Hermes for the others."""

import pytest

from revenue_agent import db
from revenue_agent.config import get_settings
from revenue_agent.llm import BudgetExceeded, reserve_call, tenant_month_usage


def test_monthly_cap_stops_a_business_and_counts_weight(monkeypatch):
    monkeypatch.setenv("TENANT_MONTHLY_LLM_CALL_CAP", "5")
    conn, s = db.connect(), get_settings()
    reserve_call(conn, None, s, "shop-a", weight=3)   # one Hermes answer
    reserve_call(conn, None, s, "shop-a")             # one direct answer
    assert tenant_month_usage(conn, "shop-a") == {"used": 4, "cap": 5}
    with pytest.raises(BudgetExceeded):
        reserve_call(conn, None, s, "shop-a", weight=3)  # would be 7 > 5
    assert tenant_month_usage(conn, "shop-a")["used"] == 4  # refused calls are not charged
    reserve_call(conn, None, s, "shop-a")
    reserve_call(conn, None, s, "shop-b", weight=3)    # other businesses are unaffected


def test_refused_daily_cap_gives_back_the_monthly_reservation(monkeypatch):
    monkeypatch.setenv("TENANT_MONTHLY_LLM_CALL_CAP", "100")
    monkeypatch.setenv("TENANT_DAILY_LLM_CALL_CAP", "2")
    conn, s = db.connect(), get_settings()
    reserve_call(conn, None, s, "shop-a", weight=2)
    with pytest.raises(BudgetExceeded):
        reserve_call(conn, None, s, "shop-a")
    assert tenant_month_usage(conn, "shop-a")["used"] == 2


def test_used_up_budget_does_not_pause_hermes_for_everyone(monkeypatch):
    from revenue_agent.profit import advisor
    monkeypatch.setenv("TENANT_MONTHLY_LLM_CALL_CAP", "0")
    monkeypatch.setattr(advisor, "hermes_ready", lambda tenant: True)
    monkeypatch.setattr(advisor, "_hermes", lambda *a, **k: pytest.fail("Hermes must not run without budget"))
    advisor._HERMES_DOWN_UNTIL[0] = 0.0
    from profit_fixtures import costs_csv, sales_csv
    from revenue_agent.profit import store
    conn = db.connect()
    iid = store.create(conn, title="x")["id"]
    for n, raw in (("s.csv", sales_csv()), ("c.csv", costs_csv())):
        store.add_file(conn, iid, n, raw, tenant="default", actor="t")
    monkeypatch.setenv("PROFIT_ADVISOR_ENGINE", "hermes")
    r = advisor.ask(conn, iid, tenant="default", question="why is my profit down?", lang="en", actor="t")
    assert r["mode"] == "rules" and "ai_budget_used_up" in (r["notice"] or "")
    assert advisor._HERMES_DOWN_UNTIL[0] == 0.0


def test_ai_settings_show_this_months_usage():
    from fastapi.testclient import TestClient
    from revenue_agent.web.app import app
    assert TestClient(app).get("/api/profit/ai-settings").json()["month_usage"] == {"used": 0, "cap": 40}
