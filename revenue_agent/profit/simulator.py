"""What-if simulator: re-run the order-level calculation for one month with explicit assumptions.

It never touches stored records (it works on copies of the order rows from orders.compute). Every result is a
PROJECTION: inputs are assumptions, and whatever the owner did not change is assumed to stay as it was in the
baseline month, including demand, unless `volume_change_pct` says otherwise. Price-change scenarios always list
"customers keep buying the same quantities" explicitly when no volume assumption is given.

Supported levers (each is checked against the data before running):
  price_change_pct        {"all": 5} or {"Shirt": 10}        needs sales lines
  product_unit_cost       {"Shirt": "180"}                   needs a known unit cost for that product
  packaging_per_order     "12.50"                            needs packaging costs in the baseline month
  shipping_per_order      "45"                               needs shipping costs in the baseline month
  discount_pct            8      (percent of gross line value)
  return_rate_pct         5      (refunds as percent of net sales)
  free_shipping           {"threshold": "800", "fee": "50"}  needs shipping charged to customers in the data
  volume_change_pct       -10    (orders scale; per-order amounts kept)
  dollar_rate             {"base": "48.5", "new": "55", "linked": {"all": 100}}
                          product unit costs move with the dollar by each product's linked share (needs unit costs)
"""

from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation

from ..ledger.csv_import import CURRENCY_EXPONENT
from . import metrics, orders as orders_mod

LEVERS = ("price_change_pct", "product_unit_cost", "packaging_per_order", "shipping_per_order", "discount_pct",
          "return_rate_pct", "free_shipping", "volume_change_pct", "dollar_rate")


class ScenarioError(ValueError):
    pass


def _minor(v, cur: str, field: str) -> int:
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ScenarioError(f"{field}: not a number") from exc
    if d < 0:
        raise ScenarioError(f"{field}: must not be negative")
    return int(d * (10 ** CURRENCY_EXPONENT.get(cur, 2)))


def _pct(v, field: str, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError) as exc:
        raise ScenarioError(f"{field}: not a number") from exc
    if not lo <= x <= hi:
        raise ScenarioError(f"{field}: must be between {lo} and {hi}")
    return x


def capabilities(o: dict, period: str) -> dict:
    """Which levers the data supports for this month, with the reason when not."""
    rows = [x for x in o["orders"] if x["period"] == period]
    cats = {c for x in rows for c in x["costs"]}
    products_with_cost = sorted({l["product"] for x in rows for l in x["lines"] if l["cogs"] is not None})
    return {
        "price_change_pct": {"ok": bool(rows)},
        "product_unit_cost": {"ok": bool(products_with_cost), "products": products_with_cost,
                              "reason": None if products_with_cost else "no product costs uploaded"},
        "packaging_per_order": {"ok": "packaging" in cats, "reason": None if "packaging" in cats else
                                "no packaging costs in this month"},
        "shipping_per_order": {"ok": "shipping" in cats, "reason": None if "shipping" in cats else
                               "no shipping costs in this month"},
        "discount_pct": {"ok": bool(rows)},
        "return_rate_pct": {"ok": bool(rows)},
        "free_shipping": {"ok": any(x["shipping_income"] > 0 for x in rows),
                          "reason": None if any(x["shipping_income"] > 0 for x in rows) else
                          "the sales file has no 'shipping charged' column, so customer delivery fees are unknown"},
        "volume_change_pct": {"ok": bool(rows)},
        "dollar_rate": {"ok": bool(products_with_cost), "reason": None if products_with_cost else
                        "no product costs uploaded"},
    }


def _with_ar(caps: dict) -> dict:
    for v in caps.values():
        if v.get("reason"):
            v["reason_ar"] = REASON_AR.get(v["reason"], v["reason"])
    return caps


REASON_AR = {
    "no product costs uploaded": "مفيش تكلفة منتجات مرفوعة",
    "no packaging costs in this month": "مفيش فواتير تغليف في الشهر ده",
    "no shipping costs in this month": "مفيش فواتير شحن في الشهر ده",
    "the sales file has no 'shipping charged' column, so customer delivery fees are unknown":
        "ملف المبيعات مفيهوش رسوم التوصيل اللي العميل دفعها، فمش معروفة",
}
UNTOUCHED_AR = {"sales": "المبيعات", "discounts": "الخصومات", "product cost": "تكلفة المنتج", "packaging": "التغليف",
                "shipping": "الشحن", "payment fees": "رسوم الدفع", "marketplace fees": "عمولات المنصات",
                "refunds": "المرتجعات", "delivery fees customers pay": "رسوم التوصيل اللي العميل بيدفعها",
                "order volume": "عدد الطلبات", "fixed costs and marketing": "المصاريف الثابتة والإعلانات"}


def _assumption_ar(a: str) -> str:
    """Egyptian-Arabic version of an assumption line (falls back to the English text)."""
    import re
    fixed = {
        "customers keep buying the same quantities at the new price (demand not measured)":
            "العملاء هيفضلوا يشتروا نفس الكميات بالسعر الجديد (رد فعلهم ماتقاسش)",
        "returned items are refunded at full value and not resold": "المرتجع بيترد بكامل قيمته ومش بيتباع تاني",
        "the dollar-linked share of each product's cost moves fully with the rate you entered":
            "الجزء المربوط بالدولار من تكلفة كل منتج بيتحرك بالكامل مع سعر الدولار اللي كتبته",
        "payment fees stay the same percentage of order value": "رسوم الدفع بتفضل نفس النسبة من قيمة الطلب",
        "some baseline costs are allocated from monthly invoices (see order profit)":
            "جزء من تكاليف الشهر متوزّع من فواتير شهرية (شوف ربح الطلبات)",
    }
    if a in fixed:
        return fixed[a]
    if a.startswith("the same orders happen at the same values"):
        return "نفس الطلبات بنفس القيم (العملاء مش هيزوّدوا عشان يوصلوا للحد، ومحدش هيلغي بسبب الرسوم)"
    if a.startswith("unchanged from the baseline month: "):
        items = a.split(": ", 1)[1].split(", ")
        return "زي ما هو في الشهر الفعلي: " + "، ".join(UNTOUCHED_AR.get(i, i) for i in items)
    m = re.match(r"order volume changes by ([+-]?[\d.]+)%", a)
    if m:
        return f"عدد الطلبات بيتغيّر {m[1]}% بنفس نوعية الطلبات وقيمها (افتراضك إنت)"
    m = re.match(r"(\d+) refunds in (\S+) don't name one of your orders", a)
    if m:
        return (f"{m[1]} مرتجع في {m[2]} مش مربوطين برقم طلب؛ اتوزّعوا على طلبات الشهر حسب قيمتها عشان الأرقام "
                "تطابق حسابات الشهر")
    return a


def _spread_unlinked_refunds(rows: list[dict], unmatched: list[dict], period: str) -> dict | None:
    """Refunds whose order ID matches no order (credit notes with their own number, return IDs) still happened in
    this month. Leaving them out would make the baseline profit too high and turn a "fewer returns" scenario into a
    loss, so they are shared across the month's orders by net value (largest-remainder, so the total is exact)."""
    items = [u for u in unmatched if (u.get("date") or "")[:7] == period]
    total = sum(u["amount"] for u in items)
    weights = [max(x["gross"] - x["discount"], 0) for x in rows]
    if not total or not sum(weights):
        return None
    shares = [total * w / sum(weights) for w in weights]
    alloc = [int(v) for v in shares]
    for i in sorted(range(len(rows)), key=lambda i: shares[i] - alloc[i], reverse=True)[:total - sum(alloc)]:
        alloc[i] += 1
    for x, a in zip(rows, alloc):
        x["refunds"] += a
    return {"count": len(items), "amount": total}


def _totals(rows: list[dict], fixed: int, cur: str) -> dict:
    t = {k: 0 for k in ("orders", "gross", "discounts", "net_sales", "shipping_income", "refunds", "cogs",
                        "packaging", "shipping", "payment_fees", "marketplace_fees", "contribution", "loss_orders")}
    for x in rows:
        w = x.get("_weight", 1.0)
        t["orders"] += w
        t["gross"] += x["gross"] * w
        t["discounts"] += x["discount"] * w
        t["net_sales"] += (x["gross"] - x["discount"]) * w
        t["shipping_income"] += x["shipping_income"] * w
        t["refunds"] += x["refunds"] * w
        t["cogs"] += x["cogs"] * w
        for c in ("packaging", "shipping", "payment_fees", "marketplace_fees"):
            t[c] += x["costs"].get(c, {}).get("amount", 0) * w
        contrib = (x["gross"] - x["discount"] + x["shipping_income"] - x["refunds"] - x["cogs"]
                   - sum(c["amount"] for c in x["costs"].values()))
        t["contribution"] += contrib * w
        t["loss_orders"] += w if contrib < 0 else 0
    t = {k: round(v) for k, v in t.items()}
    t["contribution_margin"] = metrics._ratio(t["contribution"], t["net_sales"])
    t["contribution_per_order"] = metrics._ratio(t["contribution"], t["orders"])
    t["fixed_and_marketing"] = fixed
    t["after_fixed"] = t["contribution"] - fixed if fixed else None
    cpo = t["contribution_per_order"]
    t["break_even_orders"] = round(fixed / cpo, 1) if fixed and cpo and cpo > 0 else None
    return t


def run(records: list[dict], currency: str, scenario: dict, *, period: str | None = None,
        allocation: dict | None = None, lang: str = "en") -> dict:
    unknown = set(scenario) - set(LEVERS)
    if unknown:
        raise ScenarioError(f"unknown levers: {sorted(unknown)}")
    if not scenario:
        raise ScenarioError("choose at least one change to test")
    o = orders_mod.compute(records, currency, allocation)
    periods = sorted({x["period"] for x in o["orders"]})
    if not periods:
        raise ScenarioError("no orders in this currency")
    period = period or periods[-1]
    if period not in periods:
        raise ScenarioError(f"no orders in {period}")
    caps = _with_ar(capabilities(o, period))
    for lever in scenario:
        if not caps[lever]["ok"]:
            raise ScenarioError(f"{lever} can't be tested with this data: {caps[lever].get('reason')}")
    base_rows = copy.deepcopy([x for x in o["orders"] if x["period"] == period and x["complete"]])
    excluded = sum(1 for x in o["orders"] if x["period"] == period and not x["complete"])
    unlinked = _spread_unlinked_refunds(base_rows, o["unmatched_refunds"], period)
    m = next((p for p in metrics.compute(records)["periods"] if p["period"] == period and p["currency"] == currency), None)
    fixed = (m["fixed_total"] + m["marketing"]) if m else 0
    proj = copy.deepcopy(base_rows)  # stored records are never modified
    assumptions, changed = [], []
    touched: set[str] = set()  # explicit list of what the scenario changes

    if "price_change_pct" in scenario:
        spec = scenario["price_change_pct"]
        if not isinstance(spec, dict) or not spec:
            raise ScenarioError("price_change_pct must be like {\"all\": 5} or {\"Product\": 10}")
        pcts = {k: _pct(v, "price_change_pct", -50, 100) for k, v in spec.items()}
        unknown_products = [k for k in pcts if k != "all" and not any(l["product"] == k for x in proj for l in x["lines"])]
        if unknown_products:
            raise ScenarioError(f"no sales of {unknown_products} in {period}")
        for x in proj:
            for line in x["lines"]:
                f = 1 + pcts.get(line["product"], pcts.get("all", 0.0)) / 100
                line["gross"], line["discount"] = round(line["gross"] * f), round(line["discount"] * f)
            x["gross"] = sum(l["gross"] for l in x["lines"])
            x["discount"] = sum(l["discount"] for l in x["lines"])  # discounts keep their share of the price
        changed.append("price")
        touched.update({"sales", "payment fees"})
        if "volume_change_pct" not in scenario:
            assumptions.append("customers keep buying the same quantities at the new price (demand not measured)")
    newc: dict = {}
    if "product_unit_cost" in scenario:
        spec = scenario["product_unit_cost"]
        if not isinstance(spec, dict) or not spec:
            raise ScenarioError("product_unit_cost must be like {\"Product\": \"180\"}")
        newc = {k: _minor(v, currency, f"unit cost of {k}") for k, v in spec.items()}
        bad = [k for k in newc if k not in caps["product_unit_cost"]["products"]]
        if bad:
            raise ScenarioError(f"no current unit cost for {bad}: upload product costs first")
        for x in proj:
            x["cogs"] = sum(round(newc.get(l["product"], (l["cogs"] or 0) / l["quantity"] if l["quantity"] else 0)
                                  * l["quantity"]) for l in x["lines"])
        changed.append("product_cost")
        touched.add("product cost")
    if "dollar_rate" in scenario:
        from . import fx
        spec = scenario["dollar_rate"]
        if not isinstance(spec, dict):
            raise ScenarioError('dollar_rate must be like {"base": "48.5", "new": "55", "linked": {"all": 100}}')
        try:
            fs = fx.validate({"base_rate": spec.get("base"), "current_rate": spec.get("new"),
                              "linked": spec.get("linked") or {"all": 100}})
        except fx.FxError as exc:
            raise ScenarioError(f"dollar_rate: {exc}") from exc
        if fs is None:
            raise ScenarioError("dollar_rate needs both the old and the new rate")
        ratio = fs["current_rate"] / fs["base_rate"]
        entered = newc if "product_cost" in changed else {}  # a new supplier cost entered too moves with the dollar
        for x in proj:
            x["cogs"] = sum(round(entered.get(l["product"], (l["cogs"] or 0) / l["quantity"] if l["quantity"] else 0)
                                  * (1 + fx._share(fs["linked"], l["product"]) * (ratio - 1)) * l["quantity"])
                            for l in x["lines"])
        changed.append("dollar")
        touched.add("product cost")
        assumptions.append("the dollar-linked share of each product's cost moves fully with the rate you entered")
    for lever, cat in (("packaging_per_order", "packaging"), ("shipping_per_order", "shipping")):
        if lever in scenario:
            v = _minor(scenario[lever], currency, lever)
            for x in proj:
                x["costs"][cat] = {"amount": v, "basis": "scenario"}
            changed.append(cat)
            touched.add(cat)
    if "discount_pct" in scenario:
        d = _pct(scenario["discount_pct"], "discount_pct", 0, 90)
        for x in proj:
            x["discount"] = round(x["gross"] * d / 100)
        changed.append("discount")
        touched.update({"discounts", "payment fees"})
    if "return_rate_pct" in scenario:
        r = _pct(scenario["return_rate_pct"], "return_rate_pct", 0, 100)
        for x in proj:
            x["refunds"] = round((x["gross"] - x["discount"]) * r / 100)
        changed.append("returns")
        touched.add("refunds")
        assumptions.append("returned items are refunded at full value and not resold")
    if "free_shipping" in scenario:
        fs = scenario["free_shipping"]
        if not isinstance(fs, dict) or "threshold" not in fs:
            raise ScenarioError("free_shipping needs a threshold (and the fee charged below it)")
        th = _minor(fs["threshold"], currency, "threshold")
        fee = _minor(fs.get("fee", "0"), currency, "fee")
        for x in proj:
            x["shipping_income"] = 0 if (x["gross"] - x["discount"]) >= th else fee
        changed.append("free_shipping")
        touched.add("delivery fees customers pay")
        assumptions.append("the same orders happen at the same values (customers don't add items to reach the "
                           "threshold, and none abandon because of the fee)")
    if "volume_change_pct" in scenario:
        v = _pct(scenario["volume_change_pct"], "volume_change_pct", -90, 300)
        for x in proj:
            x["_weight"] = 1 + v / 100
        changed.append("volume")
        touched.add("order volume")
        assumptions.append(f"order volume changes by {v:+g}% with the same mix and order values (your assumption)")
    # payment fees that scale with value follow the new order value when they were allocated by value
    if any(c in changed for c in ("price", "discount")):
        for b, x in zip(base_rows, proj):
            f = b["costs"].get("payment_fees")
            if f and f["basis"] in ("allocated_by_value", "actual") and b["gross"] - b["discount"]:
                x["costs"]["payment_fees"] = {"amount": round(f["amount"] * (x["gross"] - x["discount"]) /
                                                              (b["gross"] - b["discount"])), "basis": "scenario"}
        assumptions.append("payment fees stay the same percentage of order value")
    untouched = [k for k in ("sales", "discounts", "product cost", "packaging", "shipping", "payment fees",
                             "marketplace fees", "refunds", "delivery fees customers pay", "order volume",
                             "fixed costs and marketing") if k not in touched]
    assumptions.append("unchanged from the baseline month: " + ", ".join(untouched))
    if o["orders_with_allocated_costs"]:
        assumptions.append("some baseline costs are allocated from monthly invoices (see order profit)")
    if unlinked:
        assumptions.append(f"{unlinked['count']} refunds in {period} don't name one of your orders; they are shared "
                           "across the month's orders by order value so the baseline matches your monthly figures")
    base = _totals(base_rows, fixed, currency)
    new = _totals(proj, fixed, currency)
    delta = {k: round(new[k] - base[k], 4 if k == "contribution_margin" else 2)
             if isinstance(base[k], (int, float)) and isinstance(new[k], (int, float)) else None for k in base}
    return {"type": "projection", "changed": sorted(touched), "period": period, "currency": currency, "scenario": scenario, "baseline": base,
            "projected": new, "delta": delta,
            "assumptions": [_assumption_ar(a) for a in assumptions] if lang == "ar" else assumptions,
            "excluded_incomplete_orders": excluded,
            "capabilities": caps, "summary": _summary(base, new, delta, currency, period, lang, assumptions, excluded)}


def _summary(b: dict, n: dict, d: dict, cur: str, period: str, lang: str, assumptions: list[str],
             excluded: int = 0) -> str:
    from ..ledger.csv_import import money_fmt
    ar = lang == "ar"
    sign = "+" if d["contribution"] >= 0 else "−"
    amt = money_fmt(abs(d["contribution"]), cur, lang)
    note = ""
    if excluded:
        note = (f" (محسوب على {b['orders']} طلب؛ {excluded} طلب ناقصهم تكلفة منتج ومش داخلين.)" if ar else
                f" (Based on {b['orders']} orders; {excluded} orders missing a product cost are left out.)")
    if ar:
        return (f"لو طبّقت التغيير ده على طلبات {period}، اللي بيفضل لك من الطلبات هيبقى حوالي "
                f"{money_fmt(n['contribution'], cur, lang)} بدل {money_fmt(b['contribution'], cur, lang)} ({sign}{amt}). "
                "ده تقدير مبني على افتراضات، مش نتيجة حصلت، ولسه ماقسناش رد فعل العملاء." + note)
    return (f"Applied to {period}'s orders, what you keep from orders would be about "
            f"{money_fmt(n['contribution'], cur, lang)} instead of {money_fmt(b['contribution'], cur, lang)} "
            f"({sign}{amt}). This is a projection built on assumptions, not a result, and customer response "
            "has not been measured." + note)
