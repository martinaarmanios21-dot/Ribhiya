"""Dollar protection (fx.py) and the simulator's dollar_rate lever, checked against hand-calculated values.
Fixture: September has 72 orders = 48 Shirts (price 500, cost 200) and 24 Dresses (price 1000, cost 450)."""

import pytest

from profit_fixtures import costs_csv, expenses_csv, sales_csv
from revenue_agent.profit import fx, ingest, simulator


def recs():
    out = []
    for name, raw in (("s.csv", sales_csv()), ("c.csv", costs_csv()), ("e.csv", expenses_csv())):
        out += ingest.parse_file(name, raw).tables[0].records
    return out


def run(**settings):
    return fx.compute(recs(), "EGP", fx.validate({"base_rate": "50", "current_rate": "60", **settings}), "en")


def test_margin_protect_price_and_break_even_rate_match_hand_calculation():
    r = run()
    by = {p["product"]: p for p in r["products"]}
    shirt, dress = by["Shirt"], by["Dress"]
    assert r["period"] == "2026-09"
    # Shirt: cost 200 -> 240 (+20%); margin 60% -> 52%; keep 60%: 240 / 0.4 = 600; zero margin at 50 x 2.5 = 125
    assert shirt["price"] == 50000 and shirt["new_unit_cost"] == 24000
    assert (shirt["margin"], shirt["new_margin"]) == (0.6, 0.52)
    assert shirt["protect_price"] == 60000 and shirt["suggested_price"] == 60000 and shirt["raise_pct"] == 20.0
    assert shirt["break_even_rate"] == 125.0 and shirt["status"] == "squeezed"
    assert shirt["keep_profit_price"] == 54000  # same 300 EGP per unit: 500 + 40
    # Dress: cost 450 -> 540; keep 55%: 540 / 0.45 = 1200; zero margin at 50 x (1 + 1.2222) = 111.11
    assert dress["new_unit_cost"] == 54000 and dress["protect_price"] == 120000 and dress["break_even_rate"] == 111.11
    # 48 x 40 + 24 x 90 = 4,080 EGP a month lost at the same prices
    assert r["monthly_impact"] == -408000
    assert r["price_scenario"] == {"Shirt": 20.0, "Dress": 20.0}
    assert "4,080.00" in r["summary"]


def test_below_replacement_cost_is_flagged_with_loss_per_unit():
    r = fx.compute(recs(), "EGP", fx.validate({"base_rate": "50", "current_rate": "130"}), "ar")
    shirt = next(p for p in r["products"] if p["product"] == "Shirt")
    assert shirt["new_unit_cost"] == 52000 and shirt["status"] == "below_cost" and shirt["loss_per_unit"] == 2000
    assert r["counts"]["below_cost"] == 2 and r["products"][0]["status"] == "below_cost"
    assert "بأقل من تكلفة إنك تجيبهم تاني" in r["summary"]


def test_only_linked_products_move_and_unlinked_get_no_price_advice():
    r = run(linked={"Shirt": 100})
    by = {p["product"]: p for p in r["products"]}
    assert by["Dress"]["new_unit_cost"] == 45000 and by["Dress"]["status"] == "ok"
    assert by["Dress"]["suggested_price"] is None and by["Dress"]["break_even_rate"] is None
    assert r["monthly_impact"] == -48 * 4000
    partial = run(linked={"all": 50})  # half the cost is imported: 200 -> 220
    assert {p["product"]: p["new_unit_cost"] for p in partial["products"]}["Shirt"] == 22000


def test_stress_table_projects_further_rises():
    r = run()
    plus10 = next(s for s in r["stress"] if s["step_pct"] == 10)
    # rate 66 -> costs x1.32: 48 x 64 + 24 x 144 = 6,528 EGP
    assert plus10["rate"] == 66.0 and plus10["monthly_impact"] == -652800


def test_needs_rates_and_costs_before_calculating():
    assert fx.compute(recs(), "EGP", None)["needs_rates"] is True
    no_costs = [x for x in recs() if x["type"] != "product_costs"]
    assert fx.compute(no_costs, "EGP", fx.validate({"base_rate": "50", "current_rate": "60"}))["available"] is False


@pytest.mark.parametrize("bad", [{"base_rate": "0", "current_rate": "50"}, {"base_rate": "abc", "current_rate": "50"},
                                 {"base_rate": "10", "current_rate": "60"}, {"base_rate": "50", "current_rate": "60",
                                                                            "linked": {"all": 150}}])
def test_invalid_rates_are_refused(bad):
    with pytest.raises(fx.FxError):
        fx.validate(bad)


def test_simulator_dollar_lever_replays_the_month():
    r = simulator.run(recs(), "EGP", {"dollar_rate": {"base": "50", "new": "60"}})
    assert r["baseline"]["contribution"] == 2220000 and r["delta"]["contribution"] == -408000
    assert any("dollar" in a for a in r["assumptions"])
    # protect prices restore the margin: +20% on both products more than covers the cost rise
    both = simulator.run(recs(), "EGP", {"dollar_rate": {"base": "50", "new": "60"}, "price_change_pct": {"all": 20}})
    assert both["delta"]["contribution"] == 48 * 10000 + 24 * 20000 - 408000
    with pytest.raises(simulator.ScenarioError):
        simulator.run(recs(), "EGP", {"dollar_rate": {"base": "50"}})


def test_api_saves_rates_and_returns_analysis():
    from fastapi.testclient import TestClient
    from revenue_agent.web.app import app
    c = TestClient(app)
    iid = c.post("/api/investigations", json={"title": "Shop"}).json()["id"]
    c.post(f"/api/investigations/{iid}/files", files=[("files", ("s.csv", sales_csv(), "text/csv")),
                                                       ("files", ("c.csv", costs_csv(), "text/csv"))])
    assert c.get(f"/api/investigations/{iid}/fx?lang=en").json()["needs_rates"] is True
    assert c.put(f"/api/investigations/{iid}/fx", json={"base_rate": "50", "current_rate": "-1"}).status_code == 422
    ok = c.put(f"/api/investigations/{iid}/fx", json={"base_rate": "50", "current_rate": "60", "linked": {"all": 100}})
    assert ok.status_code == 200
    d = c.get(f"/api/investigations/{iid}/fx?lang=en").json()
    assert d["monthly_impact"] == -408000 and len(d["products"]) == 2
