"""Fixed shipping days: parcels that could be combined are measured from the shop's own files."""
from revenue_agent.profit import consolidation

def _sale(oid, day, cust, status="completed"):
    return {"type": "sales", "currency": "EGP", "order_id": oid, "date": f"2026-10-{day:02d}", "customer": cust,
            "status": status}

def _ship(oid, day, fee=50):
    return {"type": "expenses", "category": "shipping", "currency": "EGP", "order_id": oid,
            "date": f"2026-10-{day:02d}", "amount_minor": fee * 100}

def _shop(extra=()):
    recs = []
    for day in range(1, 13):                                   # ships every day, a different customer each day
        recs += [_sale(f"A{day}", day, f"c{day}"), _ship(f"A{day}", day)]
    return recs + list(extra)

def test_same_customer_in_the_same_half_week_is_one_parcel():
    # 2026-10-05 is a Monday, 10-06 a Tuesday (same half-week), 10-09 a Friday (other half, same week)
    recs = _shop([_sale("B1", 5, "fan"), _ship("B1", 5), _sale("B2", 6, "fan"), _ship("B2", 6),
                  _sale("B3", 9, "fan"), _ship("B3", 9)])
    est = consolidation.estimate(recs, "EGP", "2026-10")
    assert est["shipping_days"] == 12 and est["fee_per_parcel"] == 50
    assert est["twice_weekly"] == {"parcels_saved": 1, "monthly_saving": 50.0}
    assert est["weekly"] == {"parcels_saved": 2, "monthly_saving": 100.0}

def test_nothing_to_combine_or_few_shipping_days_gives_no_suggestion():
    assert consolidation.estimate(_shop(), "EGP", "2026-10") is None
    few = [_sale("X1", 5, "fan"), _ship("X1", 5), _sale("X2", 6, "fan"), _ship("X2", 6)]
    assert consolidation.estimate(few, "EGP", "2026-10") is None           # ships on 2 days only

def test_needs_a_per_parcel_courier_statement_and_ignores_unknown_customers():
    no_statement = [r for r in _shop([_sale("B1", 5, "fan"), _sale("B2", 6, "fan")]) if r["type"] == "sales"]
    assert consolidation.estimate(no_statement, "EGP", "2026-10") is None
    anon = _shop([_sale("B1", 5, None), _ship("B1", 5), _sale("B2", 6, None), _ship("B2", 6)])
    assert consolidation.estimate(anon, "EGP", "2026-10") is None

def test_option_text_carries_the_numbers_and_the_trade_off():
    est = consolidation.estimate(_shop([_sale("B1", 5, "fan"), _ship("B1", 5), _sale("B2", 6, "fan"), _ship("B2", 6)]),
                                 "EGP", "2026-10")
    fmt = lambda minor, cur, lang: f"{minor / 100:.2f} {cur}"
    ar = consolidation.option(est, "EGP", "ar", fmt); en = consolidation.option(est, "EGP", "en", fmt)
    assert ar["key"] == en["key"] == "fixed_shipping_days"
    assert "50.00 EGP" in en["tradeoffs"] and "cash-on-delivery" in en["tradeoffs"]
    assert "يومين ثابتين" in ar["title"] and "الدفع عند الاستلام" in ar["tradeoffs"]
