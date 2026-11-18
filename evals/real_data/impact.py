"""Ribhiya impact model for one Egyptian product SME. Every input is an explicit assumption; change and re-run.
usage: python3 -I impact.py
"""
FX = 55.0  # EGP per USD (bank quotes ~56 on 2026-10-26, mid-market ~54.4 on 2026-10-22)

# --- the business (typical small online seller; ASSUMPTION)
orders_per_month = 300
aov_egp = 450
net_sales = orders_per_month * aov_egp

# --- scenarios: (low, base, high)
S = {
    # hours/month an owner spends on a monthly "where did my profit go" review by hand (gather exports + invoices,
    # merge in Excel, per-order costs, compare months, find cause). Many owners skip it entirely.
    "manual_hours": (4, 7, 10),
    "ribhiya_hours": (1.0, 0.75, 0.5),       # upload, answer questions, read findings (first month is the slowest)
    "hour_value_egp": (100, 200, 300),       # owner time / freelance analyst rate (ASSUMPTION, not verified)
    # leak Ribhiya surfaces, as % of net sales. Measured on real data: Oct-2011 drivers = price erosion 2.8% +
    # refund spike 2.1% of net sales/order; test pack: discounts. Use a conservative band.
    "leak_pct": (0.02, 0.03, 0.05),
    "share_recovered": (0.25, 0.35, 0.50),    # how much of that leak the owner actually fixes
}

# --- running cost per SME per month
TOK_IN, TOK_TOTAL = 2872, 4868               # measured per advisor question (Gemini Flash, this test)
PRICE_IN, PRICE_OUT = 1.50, 9.00             # USD per 1M tokens, Gemini 3.5 Flash list price (third-party trackers)
cost_per_q_usd = TOK_IN / 1e6 * PRICE_IN + (TOK_TOTAL - TOK_IN) / 1e6 * PRICE_OUT
questions = (10, 30, 60)                      # advisor questions per SME per month
vps_usd, smes_per_vps = 6.0, (50, 150, 300)   # small VPS incl. IPv4; analysis is on demand, ~0.2-2 s per page load

print(f"Business: {orders_per_month} orders x {aov_egp} EGP = {net_sales:,} EGP net sales / month\n")
print(f"{'':28}{'low':>10}{'base':>10}{'high':>10}")
rows = {}
for i, name in enumerate(("low", "base", "high")):
    h = S["manual_hours"][i] - S["ribhiya_hours"][i]
    time_val = h * S["hour_value_egp"][i]
    profit = net_sales * S["leak_pct"][i] * S["share_recovered"][i]
    llm_free = 0.0
    llm_paid = questions[i] * cost_per_q_usd * FX
    host = vps_usd * FX / smes_per_vps[2 - i]  # high case = fewer SMEs per VPS = pessimistic cost
    rows[name] = dict(hours=h, time_val=time_val, profit=profit, llm_paid=llm_paid, host=host,
                      value=time_val + profit, cost_paid=llm_paid + host)
for k, label in [("hours", "hours saved / month"), ("time_val", "time value (EGP)"),
                 ("profit", "profit recovered (EGP)"), ("value", "total value (EGP/month)"),
                 ("host", "hosting (EGP/month)"), ("llm_paid", "LLM if paid tier (EGP)"),
                 ("cost_paid", "run cost, paid LLM (EGP)")]:
    print(f"{label:28}" + "".join(f"{rows[n][k]:>10,.1f}" for n in ("low", "base", "high")))
print(f"\nLLM cost per advisor question (paid tier): ${cost_per_q_usd:.4f} = {cost_per_q_usd * FX:.2f} EGP"
      " (free Gemini key: 0; rules-mode answers: 0)")
for n in ("low", "base", "high"):
    r = rows[n]
    print(f"{n:5} value/cost ratio: {r['value'] / r['cost_paid']:,.0f}x   yearly value {12 * r['value']:,.0f} EGP")
