"""Revenue opportunities from the sales file the owner already has: win back lapsed customers, bundle products that
sell together, and know how much of the business repeat customers carry.

Deterministic and local: no AI, nothing is sent anywhere (messages are drafts the owner copies). Customer identifiers
stay in this business's database. Every revenue figure is a PROJECTION with its assumption written next to it.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import date
from itertools import combinations
from statistics import median

from ..ledger.csv_import import money_fmt
from . import metrics

MIN_GAP_DAYS = 30          # never call a customer "lapsed" sooner than this
LAPSE_FACTOR = 2.0         # lapsed = no order for 2x the shop's typical gap between repeat orders
WINBACK_RATES = (0.05, 0.15)  # share of contacted lapsed customers assumed to order once more (low, high)
MAX_LIST = 50
MIN_HISTORY_DAYS = 90      # with less history, many "lapsed" customers are just on a longer normal cycle
HOLDOUT_SHARE = 0.3        # this share of lapsed customers gets NO message, so the real effect can be measured
MIN_GROUP = 20             # customers per group before an effect is reported
MIN_PAIR_ORDERS = 5        # a bundle needs at least this many orders containing both products
MIN_LIFT = 1.5             # ...and they must appear together 1.5x more often than chance,
MIN_SHARE = 0.6            # ...or most buyers of the less common product also take the other one


def compute(records: list[dict], currency: str, lang: str = "ar", as_of: str | None = None) -> dict:
    """as_of (YYYY-MM-DD): use only sales up to that day for the list, then measure what happened after it."""
    all_lines = [r for r in records if r["type"] == "sales" and r["currency"] == currency and r["status"] != "cancelled"]
    lines = [r for r in all_lines if not as_of or r["date"] <= as_of]
    if not lines:
        return {"available": False, "reason": "no sales"}
    orders: dict[str, dict] = {}
    for r in lines:
        o = orders.setdefault(r["order_id"], {"date": r["date"], "net": 0, "customer": None,
                                              "products": defaultdict(int)})  # product -> net spent
        o["net"] += r["gross_minor"] - r["discount_minor"]
        o["date"] = min(o["date"], r["date"])
        o["customer"] = o["customer"] or r.get("customer")
        if r.get("product"):
            o["products"][r["product"]] += r["gross_minor"] - r["discount_minor"]
    as_of = as_of or max(o["date"] for o in orders.values())
    m = [p for p in metrics.compute(records)["periods"] if p["currency"] == currency]
    net_all = sum(p["net_sales"] for p in m)
    margin = metrics._ratio(sum(p["contribution"] for p in m), net_all) if net_all else None
    history = (date.fromisoformat(as_of) - date.fromisoformat(min(o["date"] for o in orders.values()))).days
    cust = _customers(orders, as_of, margin, currency, lang, history)
    if cust.get("available") and any(r["date"] > as_of for r in all_lines):
        cust["result"] = _measure(cust, [r for r in all_lines if r["date"] > as_of], currency, lang)
    cust.pop("lapsed_all", None)  # the full list is only needed for measuring; the API returns the top MAX_LIST
    return {"available": True, "as_of": as_of, "currency": currency, "history_days": history,
            "customers": cust, "bundles": _bundles(orders, lang), "contribution_margin": margin}


def _group(customer: str) -> str:
    """Stable split (same customer -> same group every time): the comparison group gets no message."""
    h = int(hashlib.sha256(customer.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "compare" if h < HOLDOUT_SHARE else "message"


def _two_proportion_z(p1: float, n1: int, p2: float, n2: int) -> float:
    pooled = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = (pooled * (1 - pooled) * (1 / n1 + 1 / n2)) ** 0.5
    return (p1 - p2) / se if se else 0.0


def _measure(cust: dict, after: list[dict], cur: str, lang: str) -> dict:
    """What lapsed customers did after the list date, message group vs comparison group. The difference in return
    rate is the effect of the messages (if the owner messaged only the message group); both groups returning at the
    same rate means the messages added nothing."""
    spent: dict[str, int] = defaultdict(int)
    for r in after:
        if r.get("customer"):
            spent[r["customer"]] += r["gross_minor"] - r["discount_minor"]
    g = {k: [x for x in cust["lapsed_all"] if x["group"] == k] for k in ("message", "compare")}
    rate = {k: metrics._ratio(sum(1 for x in v if x["customer"] in spent), len(v)) for k, v in g.items()}
    rev = {k: sum(spent.get(x["customer"], 0) for x in v) for k, v in g.items()}
    out = {"type": "measured", "message_group": len(g["message"]), "compare_group": len(g["compare"]),
           "return_rate_message": rate["message"], "return_rate_compare": rate["compare"],
           "revenue_message": rev["message"], "revenue_compare": rev["compare"], "incremental_revenue": None}
    nm, nc = len(g["message"]), len(g["compare"])
    if min(nm, nc) < MIN_GROUP:
        out["verdict"] = "too_small"
        out["text"] = (f"المجموعتين لسه صغيرين ({nm} و{nc} عميل) عشان نعرف تأثير الرسايل بثقة. كرّر الشهر الجاي والنتايج "
                       "هتتجمع." if lang == "ar" else
                       f"The groups are still too small ({nm} and {nc} customers) to tell the effect of the messages. "
                       "Repeat next month and the results add up.")
        return out
    z = _two_proportion_z(rate["message"], nm, rate["compare"], nc)
    out["z"] = round(z, 2)
    out["incremental_revenue"] = round((rev["message"] / nm - rev["compare"] / nc) * nm)
    if abs(z) < 1.96:  # within what chance alone produces 95% of the time
        out["verdict"] = "no_clear_effect"
        out["text"] = (f"رجع {rate['message']:.0%} من اللي بعتلهم و{rate['compare']:.0%} من مجموعة المقارنة. الفرق ده "
                       "ممكن يحصل بالصدفة، فلسه مفيش تأثير واضح للرسايل." if lang == "ar" else
                       f"{rate['message']:.0%} of messaged customers came back vs {rate['compare']:.0%} of the comparison "
                       "group. A difference this size can happen by chance, so there is no clear effect yet.")
        return out
    inc = out["incremental_revenue"]
    out["verdict"] = "effect" if inc > 0 else "negative_effect"
    out["text"] = (f"من اللي بعتلهم رجع {rate['message']:.0%}، ومن اللي مابعتلهمش رجع {rate['compare']:.0%}. "
                   f"الفرق ده أكبر من الصدفة: الرسايل جابت حوالي {money_fmt(inc, cur, lang)} مبيعات زيادة." if lang == "ar"
                   else f"{rate['message']:.0%} of messaged customers came back vs {rate['compare']:.0%} of the "
                        f"comparison group, more than chance explains: the messages brought about "
                        f"{money_fmt(inc, cur, lang)} in extra sales.")
    return out


def _customers(orders: dict, as_of: str, margin: float | None, cur: str, lang: str, history: int = 0) -> dict:
    ar = lang == "ar"
    by: dict[str, list] = defaultdict(list)
    for oid, o in orders.items():
        if o["customer"]:
            by[o["customer"]].append(o)
    if not by:
        return {"available": False,
                "reason": ("ملف المبيعات مفيهوش عمود للعميل (اسم أو رقم تليفون)، فمقدرش أعرف مين رجع يشتري تاني."
                           if ar else "The sales file has no customer column (name or phone), so I can't tell who "
                                      "bought again."),
                "how_to": ("صدّر الطلبات ومعاها اسم العميل أو رقمه، وارفع الملف تاني." if ar else
                           "Export orders with the customer name or phone and upload again.")}
    covered = sum(len(v) for v in by.values()) / len(orders)
    gaps, rows, own_gap = [], [], {}
    for cust, os_ in by.items():
        ds = sorted(date.fromisoformat(o["date"]) for o in os_)
        g = [(b - a).days for a, b in zip(ds, ds[1:]) if (b - a).days > 0]
        gaps += g
        own_gap[cust] = median(g) if g else None
    typical_gap = median(gaps) if gaps else None
    lapse_days = max(MIN_GAP_DAYS, round(LAPSE_FACTOR * typical_gap)) if typical_gap else None
    end = date.fromisoformat(as_of)
    repeat = 0
    for cust, os_ in by.items():
        n = len(os_)
        repeat += n >= 2
        spent = sum(o["net"] for o in os_)
        last = max(o["date"] for o in os_)
        fav = Counter()
        for o in os_:
            fav.update(o["products"])
        fav = sorted(fav.items(), key=lambda kv: (-kv[1], kv[0]))[:1]
        rows.append({"customer": cust, "orders": n, "spent": spent, "avg_order": round(spent / n), "last_order": last,
                     "days_since": (end - date.fromisoformat(last)).days, "favourite": fav[0][0] if fav else None,
                     "own_gap_days": own_gap[cust], "group": _group(cust)})
    rows.sort(key=lambda r: -r["spent"])
    top_n = max(1, round(0.2 * len(rows)))
    total = sum(r["spent"] for r in rows)
    # lapsed: a repeat customer quiet for 2x the shop's typical gap AND 2x their own usual gap
    lapsed = [r for r in rows if lapse_days and r["orders"] >= 2 and r["days_since"] >= lapse_days and
              r["days_since"] >= LAPSE_FACTOR * (r["own_gap_days"] or 0)]
    proj = None
    to_message = [r for r in lapsed if r["group"] == "message"]
    if to_message:
        avg = sum(r["avg_order"] for r in to_message) / len(to_message)
        lo, hi = (round(len(to_message) * rate * avg) for rate in WINBACK_RATES)
        lapsed_n = len(to_message)
        proj = {"type": "projection", "contacted": lapsed_n, "revenue_low": lo, "revenue_high": hi,
                "profit_low": round(lo * margin) if margin else None, "profit_high": round(hi * margin) if margin
                else None,
                "assumptions": [f"{int(WINBACK_RATES[0] * 100)}–{int(WINBACK_RATES[1] * 100)}% of the customers you "
                                "message order once more", "at their own average order value",
                                "profit at this shop's average contribution margin" if margin else
                                "profit not estimated: product costs missing"],
                "text": (f"لو بعت لـ {lapsed_n} عميل بطّلوا يشتروا، ورجع منهم {int(WINBACK_RATES[0] * 100)}–"
                         f"{int(WINBACK_RATES[1] * 100)}% مرة واحدة بس، ده ممكن يجيب حوالي {money_fmt(lo, cur, lang)} – "
                         f"{money_fmt(hi, cur, lang)} مبيعات. ده تقدير مش نتيجة؛ قيس اللي رجع فعلاً." if ar else
                         f"If you message {lapsed_n} lapsed customers and {int(WINBACK_RATES[0] * 100)}–"
                         f"{int(WINBACK_RATES[1] * 100)}% order just once more, that is about "
                         f"{money_fmt(lo, cur, lang)} – {money_fmt(hi, cur, lang)} in sales. A projection, not a "
                         "result: measure who actually comes back.")}
    preliminary = history < MIN_HISTORY_DAYS
    return {"available": True, "preliminary": preliminary,
            "preliminary_reason": (None if not preliminary else
                                   (f"عندي {history} يوم بس من المبيعات؛ مع أقل من {MIN_HISTORY_DAYS} يوم، عملاء كتير "
                                    "باينين إنهم بطّلوا وهما بس بيشتروا على فترات أطول." if lang == "ar" else
                                    f"Only {history} days of sales; with less than {MIN_HISTORY_DAYS} days, many "
                                    "customers look lapsed but just buy less often.")),
            "how_to_measure": ("ابعت بس لمجموعة «ابعتله»، وسيب مجموعة «للمقارنة» من غير رسايل. الشهر الجاي ارفع المبيعات "
                               "الجديدة وهقولك الرسايل جابت كام فعلاً." if lang == "ar" else
                               "Message only the 'message' group and leave the 'compare' group alone. Next month upload "
                               "the new sales and I'll tell you what the messages actually brought in."),
            "customers": len(rows), "orders_with_customer": round(covered, 4),
            "repeat_customers": repeat, "repeat_rate": metrics._ratio(repeat, len(rows)),
            "top20_share": metrics._ratio(sum(r["spent"] for r in rows[:top_n]), total),
            "typical_gap_days": typical_gap, "lapse_after_days": lapse_days,
            "lapsed": lapsed[:MAX_LIST], "lapsed_all": lapsed, "lapsed_count": len(lapsed), "winback": proj,
            "message_template": _template(lang)}


def _template(lang: str) -> str:
    if lang == "ar":
        return ("أهلاً {name} 👋 وحشتنا! بقالك فترة ماطلبتش مننا. لو لسه بتحب {favourite}، عندنا تشكيلة جديدة "
                "وهنكون مبسوطين نجهزلك طلبك. تحب أبعتلك الصور؟")
    return ("Hi {name} 👋 we've missed you! It's been a while since your last order. If you still love {favourite}, "
            "we have new pieces and would be happy to prepare your order. Shall I send you photos?")


def _bundles(orders: dict, lang: str) -> list[dict]:
    multi = [set(o["products"]) for o in orders.values() if len(o["products"]) >= 2]
    n = len(orders)
    if not multi or n == 0:
        return []
    single = Counter(p for o in orders.values() for p in o["products"])
    pairs: Counter = Counter()
    for ps in multi:
        if len(ps) <= 40:  # very large wholesale orders say little about what goes together
            pairs.update(combinations(sorted(ps), 2))
    out = []
    for (a, b), k in pairs.items():
        if k < MIN_PAIR_ORDERS:
            continue
        lift = k * n / (single[a] * single[b])
        if single[b] < single[a]:
            a, b = b, a  # describe it from the less common product: "60% of coaster orders also have a mug"
        share = k / single[a]
        if lift >= MIN_LIFT or (lift > 1 and share >= MIN_SHARE):
            out.append({"products": [a, b], "orders_together": k, "lift": round(lift, 2),
                        "share_of_a": round(share, 4)})
    out.sort(key=lambda x: (-x["orders_together"], -x["lift"]))
    for x in out[:5]:
        a, b = x["products"]
        x["text"] = (f"«{a}» و«{b}» اتباعوا مع بعض في {x['orders_together']} طلب ({x['share_of_a']:.0%} من طلبات «{a}»). "
                     "اعرضهم كعرض مجمّع بتوفير بسيط." if lang == "ar" else
                     f"“{a}” and “{b}” were bought together in {x['orders_together']} orders ({x['share_of_a']:.0%} of "
                     f"“{a}” orders). Offer them as a bundle at a small saving.")
    return out[:5]
