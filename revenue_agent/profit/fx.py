"""Dollar (exchange-rate) protection: what a more expensive dollar does to each product's margin, the price that
keeps the margin, and which products are now sold for less than it costs to buy them again.

Inputs come from the owner, never guessed:
- base_rate      the dollar rate when the uploaded unit costs were valid (e.g. 48.50 EGP per USD)
- current_rate   today's rate (the owner types it, e.g. from their bank or the Central Bank of Egypt)
- linked         which share of each product's cost moves with the dollar, in percent:
                 {"all": 100} or {"Serum": 100, "Lip Balm": 60}. Products not listed use "all" (default 0 = not linked).
- target_margin_pct  optional: the product margin the owner wants to keep; default = each product's current margin

Formulas (per product, using the latest month with sales; money in minor units):
  price          = what customers actually paid per unit = (gross − discounts) / units
  new_unit_cost  = unit_cost × (1 + linked × (current_rate / base_rate − 1))
  margin         = (price − unit_cost) / price            "product margin, before packaging, shipping and fees"
  protect_price  = new_unit_cost / (1 − target margin)    the price that keeps the margin %
  keep_profit    = price + (new_unit_cost − unit_cost)    the lower option: same money per unit, not the same %
  below_cost     = price < new_unit_cost                  each unit sold can't pay for buying it again
  break_even_rate= base_rate × (1 + (price / unit_cost − 1) / linked)   the rate at which the margin reaches zero
  monthly_impact = Σ units × (new_unit_cost − unit_cost)  profit lost per month at the same prices and volume
Everything is a PROJECTION: it assumes the same units and prices as the latest month. Nothing here changes records.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from ..ledger.csv_import import CURRENCY_EXPONENT, money_fmt
from . import metrics

MIN_MARGIN = 0.20        # below this product margin a product is flagged "squeezed"
MARGIN_DROP = 0.05       # ...or when its margin falls by 5 points or more
STRESS_STEPS = (0.05, 0.10, 0.20)  # "what if the dollar rises another 5/10/20%"


class FxError(ValueError):
    pass


def _rate(v, field: str) -> float:
    try:
        x = float(Decimal(str(v).replace(",", "").strip()))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FxError(f"{field}: not a number") from exc
    if not 0 < x < 100000:
        raise FxError(f"{field}: must be a positive exchange rate")
    return x


def _pct(v, field: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError) as exc:
        raise FxError(f"{field}: not a number") from exc
    if not 0 <= x <= 100:
        raise FxError(f"{field}: must be between 0 and 100")
    return x


def validate(settings: dict | None) -> dict | None:
    """Clean owner settings, or None when they haven't set the dollar rates yet."""
    if not settings or not settings.get("base_rate") or not settings.get("current_rate"):
        return None
    base, cur = _rate(settings["base_rate"], "base_rate"), _rate(settings["current_rate"], "current_rate")
    if not 0.2 <= cur / base <= 5:
        raise FxError("current_rate: more than 5x away from base_rate; check the numbers")
    linked = settings.get("linked") if isinstance(settings.get("linked"), dict) else {"all": 100}
    linked = {str(k)[:120]: _pct(v, f"linked share of {k}") for k, v in linked.items()}
    tm = settings.get("target_margin_pct")
    tm = None if tm in (None, "") else _pct(tm, "target_margin_pct")
    if tm is not None and tm >= 95:
        raise FxError("target_margin_pct: must be below 95")
    return {"base_rate": base, "current_rate": cur, "linked": linked, "target_margin_pct": tm}


def _share(linked: dict, product: str) -> float:
    return linked.get(product, linked.get("all", 0.0)) / 100


def _nice_up(minor: float, cur: str) -> int:
    """Round a price up to a shop-friendly figure: whole units under 50, then multiples of 5."""
    unit = 10 ** CURRENCY_EXPONENT.get(cur, 2)
    major = minor / unit
    step = 1 if major < 50 else 5
    return int(math.ceil(major / step - 1e-9) * step * unit)


def _new_cost(cost: float, share: float, ratio: float) -> float:
    return cost * (1 + share * (ratio - 1))


def compute(records: list[dict], currency: str, settings: dict | None, lang: str = "ar") -> dict:
    ar = lang == "ar"
    m = metrics.compute(records)
    costs = metrics.CostTable(records, currency)
    periods = sorted({p["period"] for p in m["periods"] if p["currency"] == currency and p["orders"]})
    out: dict = {"currency": currency, "settings": settings, "period": periods[-1] if periods else None,
                 "products": [], "available": False}
    if not periods:
        out["reason"] = "ارفع ملف المبيعات الأول." if ar else "Upload your sales file first."
        return out
    if not costs:
        out["reason"] = ("محتاج تكلفة المنتجات (ملف تكلفة الوحدة) عشان أحسب أثر الدولار على كل منتج." if ar else
                         "I need your product costs (unit cost file) to work out the dollar's effect on each product.")
        return out
    out["available"] = True
    out["cost_products"] = sorted({k[0] for k in costs})
    if settings is None:
        out["needs_rates"] = True
        return out
    period = periods[-1]
    ratio = settings["current_rate"] / settings["base_rate"]
    rows = []
    for p in m["products"]:
        if p["currency"] != currency or p["period"] != period or not p["units"] or p["units"] <= 0:
            continue
        uc = costs.lookup(p["product"], currency)
        if uc is None:
            continue
        price = (p["net_sales"] + p["refunds"]) / p["units"]  # what customers paid per unit, after discounts
        if price <= 0:
            continue
        share = _share(settings["linked"], p["product"])
        new_c = _new_cost(uc, share, ratio)
        margin, new_margin = (price - uc) / price, (price - new_c) / price
        # keep today's margin (never a negative one: an already loss-making product is priced to at least break even)
        target = settings["target_margin_pct"] / 100 if settings["target_margin_pct"] is not None else max(margin, 0.0)
        protect = new_c / (1 - target) if share > 0 or settings["target_margin_pct"] is not None else None
        be_rate = (settings["base_rate"] * (1 + (price / uc - 1) / share)) if share > 0 and uc > 0 else None
        if new_c > price:
            status = "below_cost"
        elif share > 0 and (new_margin < MIN_MARGIN or margin - new_margin >= MARGIN_DROP):
            status = "squeezed"
        else:
            status = "ok"
        nice = _nice_up(protect, currency) if protect and protect > 0 else None
        rows.append({
            "product": p["product"], "units": round(p["units"], 2), "linked_pct": round(share * 100, 1),
            "price": round(price), "unit_cost": uc, "new_unit_cost": round(new_c),
            "margin": round(margin, 4), "new_margin": round(new_margin, 4),
            "protect_price": round(protect) if protect else None, "suggested_price": nice,
            # the lower option: keep the same money per unit (not the same %), i.e. pass on only the cost increase
            "keep_profit_price": _nice_up(price + new_c - uc, currency) if share > 0 and new_c > uc else None,
            "raise_pct": round((nice / price - 1) * 100, 1) if nice and abs(nice - price) >= 1 else None,
            "break_even_rate": round(be_rate, 2) if be_rate and be_rate > 0 else None,
            "loss_per_unit": round(new_c - price) if new_c > price else 0,
            "monthly_impact": -round(p["units"] * (new_c - uc)),
            "status": status,
        })
    order = {"below_cost": 0, "squeezed": 1, "ok": 2}
    rows.sort(key=lambda r: (order[r["status"]], r["monthly_impact"]))
    out["products"] = rows
    impact = sum(r["monthly_impact"] for r in rows)
    out["monthly_impact"] = impact
    out["counts"] = {s: sum(1 for r in rows if r["status"] == s) for s in order}
    out["skipped_no_cost"] = sorted({p["product"] for p in m["products"] if p["currency"] == currency and
                                     p["period"] == period and costs.lookup(p["product"], currency) is None})
    linked_rows = [r for r in rows if r["linked_pct"] > 0]
    out["stress"] = []
    for step in STRESS_STEPS:
        rate = settings["current_rate"] * (1 + step)
        rr = rate / settings["base_rate"]
        lost = -round(sum(r["units"] * (_new_cost(r["unit_cost"], r["linked_pct"] / 100, rr) - r["unit_cost"])
                          for r in rows))
        below = sum(1 for r in rows if _new_cost(r["unit_cost"], r["linked_pct"] / 100, rr) > r["price"])
        out["stress"].append({"step_pct": round(step * 100), "rate": round(rate, 2), "monthly_impact": lost,
                              "below_cost": below})
    out["price_scenario"] = {r["product"]: r["raise_pct"] for r in linked_rows
                             if r["raise_pct"] and r["raise_pct"] > 0}
    out["assumptions"] = (
        ["نفس عدد القطع ونفس أسعار البيع بتاعة آخر شهر", "الهامش هنا هامش المنتج قبل التغليف والشحن والرسوم",
         "سعر الدولار إنت اللي كتبته، ونسبة ارتباط كل منتج بالدولار إنت اللي حددتها"] if ar else
        ["same units and selling prices as the latest month", "margin here is product margin, before packaging, "
         "shipping and fees", "the dollar rates and each product's link to the dollar are the ones you entered"])
    out["summary"] = _summary(out, settings, currency, lang)
    return out


def _summary(o: dict, s: dict, cur: str, lang: str) -> str:
    ar = lang == "ar"
    rate = lambda x: f"{x:,.2f}"  # noqa: E731
    lost = money_fmt(abs(o["monthly_impact"]), cur, lang)
    below = [r["product"] for r in o["products"] if r["status"] == "below_cost"]
    squeezed = o["counts"]["squeezed"]
    move = s["current_rate"] / s["base_rate"] - 1
    if ar:
        head = (f"الدولار {'زاد' if move >= 0 else 'نزل'} من {rate(s['base_rate'])} لـ {rate(s['current_rate'])} "
                f"({move * 100:+.1f}%). ")
        if o["monthly_impact"] < 0:
            head += f"لو ماغيّرتش أسعارك، ده هياكل حوالي {lost} من ربح منتجاتك كل شهر."
        elif o["monthly_impact"] > 0:
            head += f"ده بيوفّرلك حوالي {lost} في الشهر من تكلفة المنتجات."
        else:
            head += "مفيش منتج مربوط بالدولار، فمفيش أثر محسوب."
        if below:
            head += (f" ⚠️ {len(below)} منتج بقوا بيتباعوا بأقل من تكلفة إنك تجيبهم تاني: "
                     f"{'، '.join(below[:3])}. كل قطعة بتبيعها بالسعر ده مش هتكفي تشتري بدالها.")
        if squeezed:
            head += f" و{squeezed} منتج هامشهم قلّ بشكل واضح."
        return head
    head = (f"The dollar {'rose' if move >= 0 else 'fell'} from {rate(s['base_rate'])} to {rate(s['current_rate'])} "
            f"({move * 100:+.1f}%). ")
    if o["monthly_impact"] < 0:
        head += f"If you keep your prices, this eats about {lost} of your product profit every month."
    elif o["monthly_impact"] > 0:
        head += f"This saves you about {lost} a month in product costs."
    else:
        head += "No product is linked to the dollar, so there is no effect to calculate."
    if below:
        head += (f" ⚠️ {len(below)} products now sell for less than it costs to buy them again: "
                 f"{', '.join(below[:3])}. Each unit sold at this price won't pay for its replacement.")
    if squeezed:
        head += f" {squeezed} more products lost a clear part of their margin."
    return head
