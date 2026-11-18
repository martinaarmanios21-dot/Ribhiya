"""Independent re-computation (pandas) of Ribhiya's monthly metrics from the prepared files, diffed against the
numbers Ribhiya returned. Different code path, same documented definitions (revenue_agent/profit/metrics.py).

usage: python3 -I reconcile.py prepared_dir result.json
"""
import json
import sys
from pathlib import Path

import pandas as pd

pdir, res = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
MIRROR = "--mirror" in sys.argv  # apply Ribhiya's documented skip rules (duplicates, discount > line, first cost wins)


def load(pattern, dedup_cols, money_col):
    out = []
    for f in sorted(pdir.glob(pattern)):
        d = pd.read_csv(f, dtype={"Order ID": str, "Product": str})
        if MIRROR and dedup_cols:
            d["_k"] = (d[money_col[0]] * (d[money_col[1]] if len(money_col) > 1 else 1) * 100).round()
            d = d.drop_duplicates(subset=dedup_cols + ["_k"], keep="first").drop(columns="_k")
        out.append(d)
    return pd.concat(out) if out else pd.DataFrame()


sales = load("*/sales_*.csv", ["Order ID", "Product", "Order Date"], ["Unit Price", "Quantity"])
if MIRROR:  # an order is rejected only when its total discount exceeds its total sales
    g = (sales["Unit Price"] * 100 * sales.Quantity).round()
    t = pd.DataFrame({"o": sales["Order ID"], "g": g, "d": (sales.Discount * 100).round()}).groupby("o").sum()
    sales = sales[~sales["Order ID"].isin(t[t.d > t.g].index)]
rets = load("*/returns_*.csv", ["Order ID", "Product", "Return Date"], ["Refund Amount"])
_e = [pd.read_csv(f) for f in pdir.glob("*/expenses_*.csv")]
exps = pd.concat(_e) if _e else pd.DataFrame({"Date": pd.Series(dtype=str), "Category": pd.Series(dtype=str),
                                               "Amount": pd.Series(dtype=float)})
costs = pd.read_csv(pdir / "product_costs.csv", dtype={"Product": str})
costs["k"] = costs.Product.str.strip()
exact = dict(zip(costs.k, (costs["Unit Cost"] * 100).round()))
low = costs.assign(l=costs.k.str.lower(), v=(costs["Unit Cost"] * 100).round()).groupby("l").v.agg(set)


def uc_lookup(p):
    if p in exact:
        return exact[p]
    found = low.get(p.lower(), set())
    return next(iter(found)) if len(found) == 1 else None

sales["m"] = sales["Order Date"].str[:7]
sales["gross"] = (sales["Unit Price"] * 100 * sales.Quantity).round()
sales["disc"] = (sales.Discount * 100).round()
sales["ship"] = (sales["Shipping Charged"] * 100).round()
sales["uc"] = sales.Product.str.strip().map(uc_lookup).astype(float)
sales["cogs"] = (sales.uc * sales.Quantity).round()
rets["m"] = rets["Return Date"].str[:7]
exps["m"] = exps.Date.str[:7]
cat = lambda s: "payment_fees" if "payment" in s else "marketplace_fees"  # noqa: E731
exps["cat"] = exps.Category.map(cat)

mine = {}
for m, g in sales.groupby("m"):
    orders = g["Order ID"].nunique()
    gross, disc = g.gross.sum(), g.disc.sum()
    refunds = (rets[rets.m == m]["Refund Amount"] * 100).round().sum()
    ship_inc = g.groupby("Order ID").ship.max().sum()
    cogs = g.cogs.sum()
    e = exps[exps.m == m]
    var = (pd.to_numeric(e.Amount) * 100).round().sum()
    net = gross - disc - refunds
    mine[m] = {"orders": orders, "gross_sales": gross, "discounts": disc, "refunds": refunds, "net_sales": net,
               "shipping_income": ship_inc, "cogs": cogs, "variable_total": var,
               "contribution": net + ship_inc - cogs - var,
               "return_rate": rets[rets.m == m].Quantity.sum() / g.Quantity.sum()}

theirs = {p["period"]: p for p in res["state"]["metrics"]["periods"]}
rows, worst = [], 0.0
for m, v in mine.items():
    t = theirs.get(m, {})
    for k, x in v.items():
        y = t.get(k)
        diff = None if y is None else float(y) - float(x)
        rel = abs(diff) / max(abs(float(x)), 1) if diff is not None else None
        if k != "return_rate" and rel is not None:
            worst = max(worst, rel)
        rows.append((m, k, round(float(x), 4), y, diff))
print(f"{'month':8} {'metric':16} {'independent':>16} {'ribhiya':>16} {'diff':>10}")
for r in rows:
    print(f"{r[0]:8} {r[1]:16} {r[2]:>16} {str(r[3]):>16} {str(None if r[4] is None else round(r[4], 4)):>10}")
# bridge check: independent per-order component deltas for the last two months
ms = sorted(mine)
if len(ms) >= 2:
    b, c = mine[ms[-2]], mine[ms[-1]]
    po = lambda d, k: d[k] / d["orders"]  # noqa: E731
    indep = {"discounts": -(po(c, "discounts") - po(b, "discounts")), "refunds": -(po(c, "refunds") - po(b, "refunds")),
             "shipping_income": po(c, "shipping_income") - po(b, "shipping_income"),
             "product_costs": -(po(c, "cogs") - po(b, "cogs")),
             "contribution": po(c, "contribution") - po(b, "contribution")}
    br = (res["state"].get("diagnosis") or {}).get("bridge") or {}
    print("\nbridge (per order, minor units)", ms[-2], "->", ms[-1])
    for k, v in indep.items():
        theirs_v = br.get("components", {}).get(k) if k != "contribution" else br.get("change")
        print(f"  {k:16} independent={v:10.2f}  ribhiya={theirs_v}")
print(f"\nworst relative difference across money metrics: {worst:.2e}")
