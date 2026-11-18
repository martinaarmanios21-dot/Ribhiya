"""Fixed shipping days: how many parcels a shop could combine by shipping on fixed days instead of every day.

Measured, not guessed, from the shop's own files for one month:
  parcels   = one per non-cancelled order of the month, plus courier-statement rows of the month that point to an
              older order (e.g. a subscription's monthly pack); a courier row for an order of the month replaces that
              order's parcel (refused parcels are counted too: the courier charged them)
  combine   = parcels of the SAME customer that would leave in the same shipping cycle:
                twice a week -> two buckets per ISO week (Mon–Wed, Thu–Sun)
                once a week  -> one bucket per ISO week
  saving    = combinable parcels x the month's average courier charge per parcel (from the courier statement)

Couriers in Egypt bill per parcel, so shipping on fewer days only saves when parcels can be combined (or when the
courier charges a pickup fee, which is not in the files and is therefore not counted). Waiting longer can raise
refused cash-on-delivery parcels, so the recommendation always carries that trade-off.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

MIN_SHIPPING_DAYS = 8          # only suggest it to shops that ship on most days


def _bucket(d: date, per_week: int) -> tuple:
    y, w, wd = d.isocalendar()
    return (y, w) if per_week == 1 else (y, w, 0 if wd <= 3 else 1)


def estimate(records: list[dict], currency: str, period: str) -> dict | None:
    owner, when = {}, {}
    for s in records:
        if s["type"] == "sales" and s.get("currency") == currency and s.get("status") != "cancelled" and s.get("order_id"):
            owner.setdefault(s["order_id"], s.get("customer"))
            when[s["order_id"]] = min(when.get(s["order_id"], s["date"]), s["date"])
    ship = [e for e in records if e["type"] == "expenses" and e.get("category") == "shipping"
            and e.get("currency") == currency and e["date"][:7] == period]
    linked = [e for e in ship if e.get("order_id")]
    if not linked:
        return None                                   # no per-parcel courier statement: can't measure
    fee = sum(e["amount_minor"] for e in linked) / len(linked) / 100
    parcels, covered = [], set()
    for e in linked:
        if e["order_id"] in owner:
            parcels.append((owner[e["order_id"]], date.fromisoformat(e["date"][:10])))
            covered.add(e["order_id"])
    for oid, d in when.items():
        if d[:7] == period and oid not in covered:
            parcels.append((owner.get(oid), date.fromisoformat(d[:10])))
    days = len({d for _, d in parcels})
    if days < MIN_SHIPPING_DAYS:
        return None
    out = {"period": period, "parcels": len(parcels), "shipping_days": days, "fee_per_parcel": round(fee, 2)}
    for per_week, key in ((2, "twice_weekly"), (1, "weekly")):
        groups = defaultdict(int)
        for cust, d in parcels:
            if cust:                                  # unknown customer: never combined
                groups[(cust, _bucket(d, per_week))] += 1
        saved = sum(n - 1 for n in groups.values() if n > 1)
        out[key] = {"parcels_saved": saved, "monthly_saving": round(saved * fee, 2)}
    return out if out["twice_weekly"]["parcels_saved"] or out["weekly"]["parcels_saved"] else None


def _tard(n: int) -> str:                       # Arabic plural: 3–10 takes «طرود»
    return f"{n} طرود" if 3 <= n <= 10 else f"{n} طرد"


def option(est: dict, currency: str, lang: str, fmt) -> dict:
    tw, wk = est["twice_weekly"], est["weekly"]
    if lang == "ar":
        return {"key": "fixed_shipping_days", "measured": est,
                "title": "اشحن في يومين ثابتين في الأسبوع بدل كل يوم، وجمّع طلبات نفس العميل في طرد واحد.",
                "tradeoffs": (f"في {est['period']} شحنت في {est['shipping_days']} يوم ({_tard(est['parcels'])}). لو طلبات نفس "
                              f"العميل في نفس نص الأسبوع اتجمّعت، كنت هتوفّر {_tard(tw['parcels_saved'])} ≈ "
                              f"{fmt(tw['monthly_saving'] * 100, currency, lang)} في الشهر (لو مرة واحدة في الأسبوع: "
                              f"{_tard(wk['parcels_saved'])} ≈ {fmt(wk['monthly_saving'] * 100, currency, lang)}). "
                              "كمان بيوفّر وقت التغليف، ورسوم الاستلام لو شركة الشحن بتاخدها. خلي بالك: الانتظار الأطول "
                              "ممكن يزوّد رفض الدفع عند الاستلام، وراجع وعد التوصيل على موقعك.")}
    return {"key": "fixed_shipping_days", "measured": est,
            "title": "Ship on two fixed days a week instead of every day, and combine a customer's orders into one parcel.",
            "tradeoffs": (f"In {est['period']} you shipped on {est['shipping_days']} days ({est['parcels']} parcels). "
                          f"Combining the same customer's orders within each half-week would have saved "
                          f"{tw['parcels_saved']} parcels ≈ {fmt(tw['monthly_saving'] * 100, currency, lang)} a month (once a "
                          f"week: {wk['parcels_saved']} parcels ≈ {fmt(wk['monthly_saving'] * 100, currency, lang)}). It also "
                          "saves packing time, and pickup fees if your courier charges them. Watch out: longer waits can "
                          "raise refused cash-on-delivery parcels; check the delivery promise on your website.")}
