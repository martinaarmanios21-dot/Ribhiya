"""Independent like-for-like price/cost/basket split (pandas) vs Ribhiya's bridge. usage: check_price_effect.py prepared_dir result.json"""
import glob, json, sys
import pandas as pd
pdir, res = sys.argv[1], json.load(open(sys.argv[2]))
s = []
for f in sorted(glob.glob(f"{pdir}/*/sales_*.csv")):
    d = pd.read_csv(f, dtype={"Order ID": str, "Product": str})
    d["gross"] = (d["Unit Price"] * 100 * d.Quantity).round()
    d = d.drop_duplicates(subset=["Order ID", "Product", "Order Date", "gross"])   # Ribhiya's documented dedup
    s.append(d[(d.Discount * 100).round() <= d.gross])
s = pd.concat(s); s["m"] = s["Order Date"].str[:7]
c = pd.read_csv(f"{pdir}/product_costs.csv", dtype={"Product": str})
uc = dict(zip(c.Product, (c["Unit Cost"] * 100).round()))
s["cogs"] = (s.Product.map(uc) * s.Quantity).round().fillna(0)
br = res["state"]["diagnosis"]["bridge"]; bp, cp = br["base_period"], br["current_period"]
g = s.groupby(["m", "Product"]).agg(q=("Quantity", "sum"), gross=("gross", "sum"), cogs=("cogs", "sum"))
B, C = g.loc[bp], g.loc[cp]
nb, nc = s[s.m == bp]["Order ID"].nunique(), s[s.m == cp]["Order ID"].nunique()
pc, cc = C.gross / C.q, C.cogs / C.q
pb = (B.gross / B.q).reindex(C.index).fillna(pc); cb = (B.cogs / B.q).reindex(C.index).fillna(cc)
price = (C.q * (pc - pb)).sum() / nc; cost = -(C.q * (cc - cb)).sum() / nc
basket = (C.q * (pb - cb)).sum() / nc - (B.gross.sum() - B.cogs.sum()) / nb
for k, v in (("price_and_mix", price), ("product_costs", cost), ("basket_size", basket)):
    print(f"{k:14} independent={v:10.2f}  ribhiya={br['components'][k]}")
