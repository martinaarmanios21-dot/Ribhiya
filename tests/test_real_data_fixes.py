"""Regression tests for defects found by running Ribhiya on a real retail export (UCI Online Retail II, 2026-10-29).

All business data here is fictional TEST-ONLY data built in the tests; each case reproduces the shape of a real-data
failure in miniature.
"""

import io
from datetime import date, timedelta

from revenue_agent.profit import diagnosis, ingest, metrics, opportunities, orders, simulator, store

T = "default"


def _sales(rows: list[tuple]) -> bytes:
    """rows: (order_id, date, product, qty, price, discount)"""
    out = io.StringIO()
    out.write("Order ID,Date,Product,Quantity,Unit Price,Discount\n")
    for r in rows:
        out.write(",".join(str(x) for x in r) + "\n")
    return out.getvalue().encode()


def _basket_shop(items_base: int, items_cur: int, n: int = 60, disc_pct: float = 0.0) -> list[dict]:
    rows = []
    for m, items in (("2026-08", items_base), ("2026-09", items_cur)):
        for i in range(n):
            gross = 100 * items
            rows.append((f"{m}-{i}", f"{m}-{i % 27 + 1:02d}", "Mug", items, 100, round(gross * disc_pct, 2)))
    recs = ingest.parse_file("s.csv", _sales(rows)).tables[0].records
    recs += ingest.parse_file("c.csv", b"Product,Unit Cost\nMug,40\n").tables[0].records
    exp = "Date,Category,Amount\n2026-08-05,packaging,600\n2026-09-05,packaging,600\n"
    recs += ingest.parse_file("e.csv", exp.encode()).tables[0].records
    return recs


def _diag(recs):
    m = metrics.compute(recs)
    return diagnosis.diagnose(recs, m, m["currencies"][0])


# ---------------------------------------------------------------- 1. bigger baskets are not a cost leak

def test_bigger_baskets_are_not_reported_as_a_product_cost_leak():
    d = _diag(_basket_shop(1, 3, disc_pct=0.1))  # 3x the items per order, same prices, costs and discount rate
    drivers = {f["driver"] for f in d["findings"]}
    assert "product_costs" not in drivers and "discounts" not in drivers
    comps = d["bridge"]["components"]
    assert comps["product_costs"] == 0 and comps["price_and_mix"] == 0
    assert comps["basket_size"] > 0
    assert {e["driver"] for e in d["explained_by_order_size"]} == {"discounts"}
    assert abs(sum(comps.values()) - d["bridge"]["change_per_order"]) < 0.01


def test_smaller_baskets_are_named_as_the_cause_with_revenue_options():
    d = _diag(_basket_shop(3, 1))
    f = d["findings"][0]
    assert f["driver"] == "basket_size" and f["base_value_per_order"] == 3 and f["current_value_per_order"] == 1
    recs = diagnosis.recommendations(f, d, "en")[0]
    assert {o["key"] for o in recs["options"]} >= {"bundle_offers", "free_shipping_threshold"}
    ex = diagnosis.explain(f, d, "en")
    assert "fewer or lower-margin items per order" in ex["what"] and "3.00" in ex["evidence"]
    assert "عدد القطع" in diagnosis.explain(f, d, "ar")["evidence"]


def _two_months(base_rows, cur_rows, costs=b"Product,Unit Cost\nMug,40\nVase,150\n"):
    rows = [(f"B{i}", f"2026-08-{i % 27 + 1:02d}", *r) for i, r in enumerate(base_rows)]
    rows += [(f"C{i}", f"2026-09-{i % 27 + 1:02d}", *r) for i, r in enumerate(cur_rows)]
    recs = ingest.parse_file("s.csv", _sales(rows)).tables[0].records
    return recs + ingest.parse_file("c.csv", costs).tables[0].records


def test_shift_to_pricier_items_is_not_a_product_cost_leak():
    # same prices and costs; customers now buy the 300 vase instead of the 100 mug (the small-shop real-data case)
    d = _diag(_two_months([("Mug", 2, 100, 0)] * 40, [("Vase", 1, 300, 0)] * 40))
    assert "product_costs" not in {f["driver"] for f in d["findings"]}
    comps = d["bridge"]["components"]
    assert comps["product_costs"] == 0 and comps["price_and_mix"] == 0 and comps["basket_size"] == 3000
    assert abs(sum(comps.values()) - d["bridge"]["change_per_order"]) < 0.01


def test_like_for_like_price_cut_is_named_as_price():
    # the full real-data case: the same product sold for less
    d = _diag(_two_months([("Mug", 2, 100, 0)] * 40, [("Mug", 2, 80, 0)] * 40))
    f = d["findings"][0]
    assert f["driver"] == "price_and_mix" and f["change_per_order"] == -4000
    assert {x["driver"] for x in d["findings"]} == {"price_and_mix"}
    assert "sell the same products for less" in diagnosis.explain(f, d, "en")["what"]


# ---------------------------------------------------------------- 2. refunds that name no order

def _shop_with_credit_notes() -> list[dict]:
    rows = [(f"O{i}", f"2026-09-{i % 27 + 1:02d}", "Mug", 2, 100, 0) for i in range(50)]
    recs = ingest.parse_file("s.csv", _sales(rows)).tables[0].records
    recs += ingest.parse_file("c.csv", b"Product,Unit Cost\nMug,40\n").tables[0].records
    # credit notes carry their own number (like C570556), not the original order ID: 10% of sales refunded
    ret = "Order ID,Return Date,Product,Quantity,Refund Amount\n" + "".join(
        f"C{i},2026-09-{i + 1:02d},Mug,2,200\n" for i in range(5))
    recs += ingest.parse_file("r.csv", ret.encode()).tables[0].records
    return recs


def test_simulator_baseline_includes_refunds_that_name_no_order():
    recs = _shop_with_credit_notes()
    m = next(p for p in metrics.compute(recs)["periods"] if p["period"] == "2026-09")
    r = simulator.run(recs, "EGP", {"price_change_pct": {"all": 0}}, period="2026-09")
    assert r["baseline"]["refunds"] == m["refunds"] == 100000
    assert r["baseline"]["contribution"] == m["contribution"]
    assert any("don't name one of your orders" in a for a in r["assumptions"])


def test_fewer_returns_scenario_is_a_gain_not_a_loss():
    r = simulator.run(_shop_with_credit_notes(), "EGP", {"return_rate_pct": 2}, period="2026-09")
    assert r["delta"]["refunds"] < 0 and r["delta"]["contribution"] > 0  # 10% -> 2% of net sales
    assert r["delta"]["contribution"] == 80000


# ---------------------------------------------------------------- 3. product insights compare margins, not units

def test_top_seller_with_a_normal_margin_is_not_called_a_weak_earner():
    rows = [(f"M{i}", "2026-09-05", "Mug", 10, 12, 0) for i in range(40)]           # cheap best seller, 50% margin
    rows += [("X1", "2026-09-06", "Mirror", 1, 165, 0)]                              # one pricey sale, 50% margin
    recs = ingest.parse_file("s.csv", _sales(rows)).tables[0].records
    recs += ingest.parse_file("c.csv", b"Product,Unit Cost\nMug,6\nMirror,82.5\n").tables[0].records
    ins = {i["kind"] for i in orders.insights(orders.compute(recs, "EGP"), "en")}
    assert "top_seller_not_top_earner" not in ins


def test_one_off_loss_on_a_single_order_is_not_a_loss_product():
    rows = [(f"M{i}", "2026-09-05", "Mug", 1, 100, 0) for i in range(20)]
    rows += [("Z1", "2026-09-06", "Vase", 1, 100, 90)]                               # one heavily discounted sale
    recs = ingest.parse_file("s.csv", _sales(rows)).tables[0].records
    recs += ingest.parse_file("c.csv", b"Product,Unit Cost\nMug,40\nVase,40\n").tables[0].records
    ins = {i["kind"] for i in orders.insights(orders.compute(recs, "EGP"), "en")}
    assert "loss_product" not in ins


# ---------------------------------------------------------------- 4. raw exports straight from the shop's system

RAW = (b"Invoice,StockCode,Description,Quantity,InvoiceDate,Price,Customer ID,Country\n"
       b"1001,85123A,WHITE HEART,6,2026-09-01 08:26:00,2.55,17850,United Kingdom\n"
       b"1001,POST,POSTAGE,1,2026-09-01 08:26:00,18.00,17850,United Kingdom\n"
       b"1002,22423,CAKESTAND,2,2026-09-02 10:00:00,12.75,13047,United Kingdom\n"
       b"C1003,22423,CAKESTAND,-1,2026-09-05 11:00:00,12.75,13047,United Kingdom\n"
       b"1004,POST,POSTAGE,1,2026-09-06 09:00:00,40.00,12583,France\n")


def test_raw_pos_export_is_recognised_without_manual_mapping():
    t = ingest.parse_file("export.csv", RAW).tables[0]
    assert t.detected_type == "sales"
    assert {"order_id", "date", "product", "quantity", "unit_price", "customer"} <= set(t.mapping)


def test_credit_note_lines_become_returns_and_postage_becomes_delivery_income():
    t = ingest.parse_file("export.csv", RAW).tables[0]
    sales = [r for r in t.records if r["type"] == "sales"]
    returns = [r for r in t.records if r["type"] == "returns"]
    assert [(r["order_id"], r["refund_minor"], r["quantity"]) for r in returns] == [("C1003", 1275, 1.0)]
    assert {r["product"] for r in sales} == {"85123A", "22423"}                 # postage is not a product
    first = next(r for r in sales if r["order_id"] == "1001")
    assert first["shipping_charged_minor"] == 1800 and first["customer"] == "17850"
    codes = {i.code for i in t.issues}
    assert {"read_as_return", "delivery_line", "delivery_only_order"} <= codes
    assert not [i for i in t.issues if i.severity == "error"]
    m = metrics.compute(t.records)["periods"][0]
    assert m["gross_sales"] == 1530 + 2550 and m["refunds"] == 1275 and m["shipping_income"] == 1800


def test_order_level_discount_on_one_line_is_kept_but_impossible_discount_is_rejected():
    rows = [("A", "2026-09-01", "Mug", 1, 100, 0), ("A", "2026-09-01", "Cup", 1, 20, 50),   # 50 off a 120 order
            ("B", "2026-09-01", "Cup", 1, 20, 50)]                                           # 50 off a 20 order
    t = ingest.parse_file("s.csv", _sales(rows)).tables[0]
    assert sorted({r["order_id"] for r in t.records}) == ["A"]
    assert [i.code for i in t.issues if i.severity == "error"] == ["discount_exceeds_revenue"]
    assert metrics.compute(t.records)["periods"][0]["net_sales"] == 7000


def test_skus_that_differ_only_by_case_keep_their_own_costs():
    rows = [("A", "2026-09-01", "15056BL", 1, 10, 0), ("B", "2026-09-01", "15056bl", 1, 20, 0),
            ("C", "2026-09-01", "Mug", 1, 10, 0)]
    recs = ingest.parse_file("s.csv", _sales(rows)).tables[0].records
    c = ingest.parse_file("c.csv", b"Product,Unit Cost\n15056BL,3\n15056bl,8\nMUG,4\n").tables[0]
    assert not [i for i in c.issues if i.code == "duplicate_record"]
    p = metrics.compute(recs + c.records)["periods"][0]
    assert p["cogs"] == 300 + 800 + 400 and p["cogs_coverage"] == 1.0   # "Mug" matches "MUG" (unambiguous)


# ---------------------------------------------------------------- 5. currency mismatch is said out loud

def test_costs_in_another_currency_produce_a_clear_first_action(conn):
    iid = store.create(conn, title="UK shop")["id"]
    store.add_file(conn, iid, "s.csv", _sales([("A", "2026-09-01", "Mug", 1, 10, 0)]), tenant=T, actor="t")
    store.add_file(conn, iid, "c.csv", b"Product,Unit Cost,Currency\nMug,4,GBP\n", tenant=T, actor="t")
    st = diagnosis.state(conn, iid, T, "en")
    assert st["currency_mismatch"] == {"sales_currency": "EGP", "cost_currencies": ["GBP"]}
    assert st["actions"][0]["kind"] == "currency_mismatch" and "GBP" in st["actions"][0]["next"]


def test_one_big_return_is_flagged_as_possibly_one_off():
    rows = [(f"{m}-{i}", f"{m}-{i % 27 + 1:02d}", "Mug", 1, 100, 0) for m in ("2026-08", "2026-09") for i in range(40)]
    recs = ingest.parse_file("s.csv", _sales(rows)).tables[0].records
    recs += ingest.parse_file("c.csv", b"Product,Unit Cost\nMug,40\n").tables[0].records
    ret = ("Order ID,Return Date,Refund Amount\n2026-08-1,2026-08-10,100\n2026-09-1,2026-09-10,100\n"
           "2026-09-2,2026-09-11,900\n")                                               # one wholesale-sized return
    recs += ingest.parse_file("r.csv", ret.encode()).tables[0].records
    d = _diag(recs)
    f = next(x for x in d["findings"] if x["driver"] == "refunds")
    caveats = diagnosis.explain(f, d, "en")["caveats"]
    assert any("2026-09-2" in c and "90%" in c and "one-off" in c for c in caveats)


def test_fee_lines_on_credit_notes_are_costs_not_refunds():
    raw = (b"Invoice,StockCode,Description,Quantity,InvoiceDate,Price\n"
           b"1,22423,CAKESTAND,2,2026-09-01,12.75\n"
           b"C2,AMAZONFEE,AMAZON FEE,-1,2026-09-07,5942.57\n"
           b"C3,BANK CHARGES,Bank Charges,-1,2026-09-10,15.00\n")
    t = ingest.parse_file("export.csv", raw).tables[0]
    fees = {r["category"]: r["amount_minor"] for r in t.records if r["type"] == "expenses"}
    assert fees == {"marketplace_fees": 594257, "payment_fees": 1500}
    assert not [r for r in t.records if r["type"] == "returns"]
    m = metrics.compute(t.records)["periods"][0]
    assert m["refunds"] == 0 and m["variable"]["marketplace_fees"] == 594257


def test_description_column_catches_delivery_fee_and_manual_adjustment_codes():
    raw = (b"Invoice,StockCode,Description,Quantity,InvoiceDate,Price\n"
           b"1,22423,CAKESTAND,2,2026-09-01,12.75\n"
           b"1,DOT,DOTCOM POSTAGE,1,2026-09-01,30.00\n"
           b"2,M,Manual,1,2026-09-02,100.00\n"
           b"C3,M,Manual,-1,2026-09-03,90.00\n"
           b"C4,CRUK,CRUK Commission,-1,2026-09-04,40.00\n")
    t = ingest.parse_file("export.csv", raw).tables[0]
    assert [r["type"] for r in t.records] == ["sales", "expenses"]
    assert t.records[0]["shipping_charged_minor"] == 3000 and t.records[1]["category"] == "marketplace_fees"
    adj = next(i for i in t.issues if i.code == "adjustment_lines")
    assert "2 manual adjustment lines (net +10.00)" in adj.message
    assert "سطر تسوية يدوية" in adj.as_dict()["message_ar"]


# ---------------------------------------------------------------- revenue opportunities (win-back, bundles)

def _customer_sales() -> list[dict]:
    out = io.StringIO()
    out.write("Order ID,Date,Product,Quantity,Unit Price,Customer\n")
    n = 0
    for c in range(20):                      # 20 regulars buying every ~14 days until mid-July...
        last_day = 90 if c < 10 else 30      # ...10 of them stopped after day 30 (lapsed)
        for day in range(0, last_day + 1, 14):
            n += 1
            d = date(2026, 6, 1) + timedelta(days=day)
            out.write(f"O{n},{d},Mug,1,100,cust{c}\n")
            if c % 2 == 0:
                out.write(f"O{n},{d},Coaster,2,20,cust{c}\n")   # mugs and coasters go together
    for i in range(30):                       # one-off buyers of other things
        n += 1
        out.write(f"O{n},2026-08-{i % 28 + 1:02d},Vase,1,150,walkin{i}\n")
    recs = ingest.parse_file("s.csv", out.getvalue().encode()).tables[0].records
    return recs + ingest.parse_file("c.csv", b"Product,Unit Cost\nMug,40\nCoaster,5\nVase,60\n").tables[0].records


def test_winback_list_finds_regulars_who_stopped_buying():
    r = opportunities.compute(_customer_sales(), "EGP", "en")
    c = r["customers"]
    assert c["typical_gap_days"] == 14 and c["lapse_after_days"] == 30
    assert c["lapsed_count"] == 10 and {x["customer"] for x in c["lapsed"]} == {f"cust{i}" for i in range(10, 20)}
    assert c["lapsed"][0]["favourite"] == "Mug" and c["repeat_rate"] == 0.4
    w = c["winback"]
    assert w["type"] == "projection" and w["revenue_low"] < w["revenue_high"] and "projection" in w["text"]
    assert "{name}" in c["message_template"]


def test_bundles_are_products_bought_together_more_than_by_chance():
    b = opportunities.compute(_customer_sales(), "EGP", "en")["bundles"]
    assert b[0]["products"] == ["Coaster", "Mug"] and b[0]["share_of_a"] == 1.0
    assert "Coaster" in b[0]["text"] and "100%" in b[0]["text"]


def test_no_customer_column_says_what_to_export():
    r = opportunities.compute(_basket_shop(1, 2), "EGP", "ar")
    assert r["customers"]["available"] is False and "للعميل" in r["customers"]["reason"]


def test_opportunities_endpoint_is_scoped_to_the_business(monkeypatch):
    from fastapi.testclient import TestClient
    from revenue_agent.web.app import app
    monkeypatch.setenv("TENANT_TOKENS", "acme:acme-token-123456,beta:beta-token-123456")
    A, B = {"X-Tahsila-Token": "acme-token-123456"}, {"X-Tahsila-Token": "beta-token-123456"}
    c = TestClient(app)
    iid = c.post("/api/investigations", headers=A, json={"title": "Shop"}).json()["id"]
    raw = b"Order ID,Date,Product,Quantity,Unit Price,Customer\nA,2026-09-01,Mug,1,100,Mona\n"
    c.post(f"/api/investigations/{iid}/files", headers=A, files=[("files", ("s.csv", raw, "text/csv"))])
    assert c.get(f"/api/investigations/{iid}/opportunities?lang=en", headers=A).json()["available"] is True
    assert c.get(f"/api/investigations/{iid}/opportunities", headers=B).status_code == 404


def _lapsed_shop(back) -> list[dict]:
    """80 regulars buying every 10 days in June, then silent; back(group) says who orders again in September."""
    out = io.StringIO()
    out.write("Order ID,Date,Product,Quantity,Unit Price,Customer\n")
    n = 0
    for c in range(80):
        for day in range(0, 31, 10):
            n += 1
            out.write(f"O{n},{date(2026, 4, 1) + timedelta(days=day)},Mug,1,100,cust{c}\n")
        if back(opportunities._group(f"cust{c}")):
            n += 1
            out.write(f"O{n},2026-09-10,Mug,1,100,cust{c}\n")
    return ingest.parse_file("s.csv", out.getvalue().encode()).tables[0].records


def test_comparison_group_measures_real_extra_sales():
    r = opportunities.compute(_lapsed_shop(lambda g: g == "message"), "EGP", "en", as_of="2026-08-31")
    res = r["customers"]["result"]
    assert res["return_rate_message"] == 1.0 and res["return_rate_compare"] == 0.0
    assert res["incremental_revenue"] == res["message_group"] * 10000 and res["verdict"] == "effect"
    assert "more than chance" in res["text"]


def test_customers_who_come_back_anyway_show_no_effect():
    res = opportunities.compute(_lapsed_shop(lambda g: True), "EGP", "en", as_of="2026-08-31")["customers"]["result"]
    assert res["return_rate_message"] == res["return_rate_compare"] == 1.0 and res["incremental_revenue"] == 0
    assert res["verdict"] == "no_clear_effect"


def test_small_groups_never_announce_an_effect():
    rows = [r for r in _lapsed_shop(lambda g: g == "message") if int(r.get("customer", "cust0")[4:]) < 30]
    res = opportunities.compute(rows, "EGP", "en", as_of="2026-08-31")["customers"]["result"]
    assert res["verdict"] == "too_small" and res["incremental_revenue"] is None


def test_groups_are_stable_and_short_history_is_preliminary():
    assert opportunities._group("cust7") == opportunities._group("cust7")
    assert opportunities.compute(_lapsed_shop(lambda g: True), "EGP", "en")["customers"]["preliminary"] is False
    c = opportunities.compute(_customer_sales(), "EGP", "en")["customers"]          # 88 days of history
    assert c["preliminary"] is True and "88 days" in c["preliminary_reason"]


def test_ids_that_went_through_excel_lose_the_trailing_point_zero():
    raw = b"Invoice,InvoiceDate,StockCode,Quantity,Price,Customer ID\n536365.0,2026-09-01,85123A,1,2.5,17850.0\n"
    r = ingest.parse_file("x.csv", raw).tables[0].records[0]
    assert r["order_id"] == "536365" and r["customer"] == "17850"


def test_price_evidence_is_like_for_like_not_the_average_item_price():
    # mug price cut 100 -> 80 while customers also add a pricey vase: the average price per item RISES,
    # but the same products got cheaper, and the evidence must say so
    d = _diag(_two_months([("Mug", 2, 100, 0)] * 40, [("Mug", 2, 80, 0)] * 40 + [("Vase", 1, 300, 0)] * 40))
    f = next(x for x in d["findings"] if x["driver"] == "price_and_mix")
    assert d["bridge"]["like_for_like"]["price_pct"] == -0.2
    ev = diagnosis.explain(f, d, "en")["evidence"]
    assert "-20.0%" in ev and "same products" in ev
    assert "-20.0%" in diagnosis.explain(f, d, "ar")["evidence"]


def test_product_margin_chart_skips_products_swamped_by_old_returns():
    rows = [(f"B{i}", "2026-08-05", "Mug", 2, 100, 0) for i in range(40)] + \
           [(f"C{i}", "2026-09-05", "Mug", 2, 100, 0) for i in range(40)] + [("C99", "2026-09-06", "Vase", 1, 10, 0)]
    recs = ingest.parse_file("s.csv", _sales(rows)).tables[0].records
    recs += ingest.parse_file("c.csv", b"Product,Unit Cost\nMug,40\nVase,4\n").tables[0].records
    # a big refund of vases sold months ago lands in September: the vase "margin" would be about -1500%
    recs += ingest.parse_file("r.csv", b"Order ID,Return Date,Product,Quantity,Refund Amount\nOLD1,2026-09-10,Vase,15,150\n").tables[0].records
    m = metrics.compute(recs)
    chart = next(c for c in diagnosis.charts(m, _diag(recs), "EGP") if c["id"] == "products")
    assert [i["product"] for i in chart["items"]] == ["Mug"]


def test_shopify_order_export_is_read_without_mapping():
    hdr = ("Name,Email,Financial Status,Paid at,Currency,Subtotal,Shipping,Total,Discount Amount,Created at,"
           "Lineitem quantity,Lineitem name,Lineitem price,Lineitem sku")
    rows = ["#1001,a@x.test,paid,2026-11-02,EGP,206.00,0.00,206.00,0.00,2026-11-02 10:00:00 +0200,1,The Whole Squad,168.00,SQ12",
            "#1001,a@x.test,,,EGP,,,,,2026-11-02 10:00:00 +0200,2,Extra sticker,19.00,EX1",
            "#1002,b@x.test,paid,2026-11-03,EGP,89.00,70.00,159.00,0.00,2026-11-03 12:00:00 +0200,1,Signed Chloe Card,89.00,CARD"]
    t = ingest.parse_file("orders_export.csv", ("\n".join([hdr, *rows]) + "\n").encode()).tables[0]
    assert t.detected_type == "sales" and t.mapping["order_id"] == "Name" and "revenue" not in t.mapping
    m = metrics.compute(t.records)["periods"][0]
    assert m["orders"] == 2 and m["gross_sales"] == 16800 + 3800 + 8900   # totals were not double-counted
    assert m["shipping_income"] == 7000


def test_order_id_column_beats_a_generic_name_column():
    raw = b"Name,Order ID,Date,Quantity,Unit Price\nMona,A1,2026-09-01,1,100\n"
    t = ingest.parse_file("s.csv", raw).tables[0]
    assert t.mapping["order_id"] == "Order ID"
