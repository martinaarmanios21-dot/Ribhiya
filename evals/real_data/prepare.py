"""Turn a slice of the real UCI Online Retail II export into Ribhiya test files.

usage: python3 -I prepare.py slice.csv outdir [sample_frac]

Two variants per month:
  raw/       the export exactly as the shop's system produced it (only split into <=19,000-row chunks on invoice
             boundaries because Ribhiya caps a table at 20,000 rows)
  prepared/  what a cooperative owner would upload using Ribhiya's templates:
             sales (normal invoices, postage moved to shipping_charged, Discount credits attached to the customer's
             latest order), returns (credit-note product lines), expenses (Amazon fees, bank charges, CRUK
             commission found in the export), product_costs (ASSUMPTION: 50% of the product's median price).
Nothing here is invented except the product costs, which are written to ASSUMPTIONS.txt.
"""
import io
import json
import sys
from pathlib import Path

import pandas as pd

src, out = Path(sys.argv[1]), Path(sys.argv[2])
frac = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
text = src.read_text(encoding="utf-8", errors="replace")
lines = text.split("\n")
lines = lines[1:-1]  # byte-range slice: first and last lines are partial
header = "Invoice,StockCode,Description,Quantity,InvoiceDate,Price,Customer ID,Country"
df = pd.read_csv(io.StringIO(header + "\n" + "\n".join(lines)), dtype={"Invoice": str, "StockCode": str,
                                                                       "Customer ID": str})
df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"])
df["month"] = df["InvoiceDate"].dt.strftime("%Y-%m")
months = sorted(m for m in df["month"].unique() if (df["month"] == m).sum() > 1000)
# keep only complete months (slice edges are partial)
months = [m for m in months if df.loc[df.month == m, "InvoiceDate"].dt.day.min() <= 2
          and df.loc[df.month == m, "InvoiceDate"].dt.day.max() >= 28]
df = df[df.month.isin(months)].copy()
if frac < 1:
    cust = df["Customer ID"].dropna().unique()
    keep = set(pd.Series(cust).sample(frac=frac, random_state=7))
    df = df[df["Customer ID"].isin(keep)].copy()

SHIP = {"POST", "DOT", "C2"}
FEES = {"AMAZONFEE": "marketplace commission amazon", "BANK CHARGES": "bank card payment fee",
        "CRUK": "marketplace commission CRUK"}
NON_PRODUCT = SHIP | set(FEES) | {"D", "M", "m", "S", "B", "ADJUST", "ADJUST2", "TEST001", "TEST002", "PADS"}
code = df["StockCode"].str.strip()
df["code"] = code
df["is_credit"] = df["Invoice"].str.startswith("C")
df["is_adjust"] = df["Invoice"].str.startswith("A")
df["line"] = (df["Quantity"] * df["Price"]).round(2)

stats = {"months": months, "sample_frac": frac, "rows_in": len(df)}
for m in months:
    md = df[df.month == m]
    # ---------- raw variant: as exported, chunked on invoice boundaries
    rd = out / "raw" / m
    rd.mkdir(parents=True, exist_ok=True)
    cols = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]
    chunk, n, part = [], 0, 1
    for inv, g in md.groupby("Invoice", sort=False):
        if n + len(g) > 19000 and chunk:
            pd.concat(chunk)[cols].to_csv(rd / f"export_{m}_part{part}.csv", index=False)
            chunk, n, part = [], 0, part + 1
        chunk.append(g)
        n += len(g)
    if chunk:
        pd.concat(chunk)[cols].to_csv(rd / f"export_{m}_part{part}.csv", index=False)

    # ---------- prepared variant
    pdir = out / "prepared" / m
    pdir.mkdir(parents=True, exist_ok=True)
    normal = md[~md.is_credit & ~md.is_adjust]
    prod = normal[~normal.code.isin(NON_PRODUCT) & (normal.Quantity > 0) & (normal.Price > 0)].copy()
    bad = normal[~normal.code.isin(NON_PRODUCT) & ((normal.Quantity <= 0) | (normal.Price <= 0))]
    ship = normal[normal.code.isin(SHIP) & (normal.Quantity > 0)].groupby("Invoice")["line"].sum()
    prod["shipping_charged"] = prod["Invoice"].map(ship).fillna(0)
    # put the delivery fee on the first line only (the metric takes the max per order, so either is fine)
    prod.loc[prod.duplicated("Invoice"), "shipping_charged"] = 0
    orders_without_product = sorted(set(ship.index) - set(prod.Invoice))
    # Discount credit notes (code D) -> discount on the customer's latest order on/before that date
    prod["discount"] = 0.0
    disc = md[md.code.eq("D")]
    disc_unattached = 0.0
    for _, r in disc.iterrows():
        amt = abs(r["line"])
        cand = prod[(prod["Customer ID"] == r["Customer ID"]) & (prod.InvoiceDate <= r.InvoiceDate)]
        if pd.isna(r["Customer ID"]) or cand.empty:
            disc_unattached += amt
            continue
        idx = cand.sort_values("InvoiceDate").index[-1]
        prod.loc[idx, "discount"] += amt
    prod["channel"] = prod["Country"].map(lambda c: "UK" if c == "United Kingdom" else "International")
    sales = pd.DataFrame({"Order ID": prod.Invoice, "Order Date": prod.InvoiceDate.dt.strftime("%Y-%m-%d"),
                          "Product": prod.code, "Quantity": prod.Quantity, "Unit Price": prod.Price,
                          "Discount": prod.discount.round(2), "Shipping Charged": prod.shipping_charged.round(2),
                          "Channel": prod.channel, "Currency": "GBP"})
    for i, (lo, hi) in enumerate([(j, j + 19000) for j in range(0, len(sales), 19000)], 1):
        sales.iloc[lo:hi].to_csv(pdir / f"sales_{m}_part{i}.csv", index=False)
    cred = md[md.is_credit & ~md.code.isin(NON_PRODUCT)]
    returns = pd.DataFrame({"Order ID": cred.Invoice, "Return Date": cred.InvoiceDate.dt.strftime("%Y-%m-%d"),
                            "Product": cred.code, "Quantity": cred.Quantity.abs(),
                            "Refund Amount": cred.line.abs().round(2), "Currency": "GBP"})
    returns.to_csv(pdir / f"returns_{m}.csv", index=False)
    fees = md[md.code.isin(FEES)]
    exp = pd.DataFrame({"Date": fees.InvoiceDate.dt.strftime("%Y-%m-%d"),
                        "Category": fees.code.map(FEES), "Supplier": fees.code,
                        # fee rows on credit notes are costs to the shop; on normal invoices they are recharges
                        "Amount": fees.line.abs().where(fees.is_credit, -fees.line.abs()).round(2),
                        "Reference": fees.Invoice, "Currency": "GBP"})
    exp_cost = exp[exp.Amount > 0]
    exp_cost.to_csv(pdir / f"expenses_{m}.csv", index=False)
    stats[m] = {"raw_rows": len(md), "raw_invoices": md.Invoice.nunique(),
                "credit_notes": int(md[md.is_credit].Invoice.nunique()), "adjust_rows": int(md.is_adjust.sum()),
                "sales_lines": len(sales), "orders": int(sales["Order ID"].nunique()),
                "bad_rows_dropped": len(bad), "return_lines": len(returns), "expense_rows": len(exp_cost),
                "expense_rows_negative_dropped": int((exp.Amount <= 0).sum()),
                "discount_unattached": round(disc_unattached, 2),
                "postage_only_orders_dropped": len(orders_without_product),
                "sales_files": len(range(0, len(sales), 19000))}

# product costs: ASSUMPTION, one list for the whole window
allprod = df[~df.is_credit & ~df.is_adjust & ~df.code.isin(NON_PRODUCT) & (df.Quantity > 0) & (df.Price > 0)]
costs = (allprod.groupby("code")["Price"].median() * 0.5).round(2).reset_index()
costs.columns = ["Product", "Unit Cost"]
costs["Currency"] = "GBP"
(out / "prepared").mkdir(parents=True, exist_ok=True)
costs.to_csv(out / "prepared" / "product_costs.csv", index=False)
(out / "ASSUMPTIONS.txt").write_text(
    "Source: UCI Online Retail II (real UK online gift retailer, CC BY 4.0), months " + ", ".join(months) + ".\n"
    "Only invented input: product unit cost = 50% of the product's median selling price over these months.\n"
    "Not in the source data, so not uploaded: packaging, seller's own shipping cost, rent/salaries, marketing.\n")
(out / "prep_stats.json").write_text(json.dumps(stats, indent=1, default=str))
print(json.dumps(stats, indent=1, default=str))
