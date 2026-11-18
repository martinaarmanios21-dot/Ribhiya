"""Backtest revenue opportunities on the real export: run with data up to a cut-off, check what happened next month.
usage: uv run --project ~/revenue-agent python -I backtest_opportunities.py raw_dir"""
import glob, json, sys
from revenue_agent.profit import ingest, opportunities
recs = []
for f in sorted(glob.glob(f"{sys.argv[1]}/*/export_*.csv")):
    for t in ingest.parse_file(f, open(f, "rb").read(), default_currency="GBP").tables:
        recs += t.records
recs += ingest.parse_file("c.csv", open(f"{sys.argv[1]}/product_costs.csv", "rb").read()).tables[0].records
before = [r for r in recs if r["type"] == "product_costs" or (r.get("date") or "") < "2011-10-01"]
r = opportunities.compute(before, "GBP", "en")
c = r["customers"]
print(json.dumps({k: c[k] for k in ("customers", "orders_with_customer", "repeat_rate", "top20_share",
                                      "typical_gap_days", "lapse_after_days", "lapsed_count")}))
print("winback projection:", c["winback"]["text"])
oct_ = {}
for x in recs:
    if x["type"] == "sales" and x["date"].startswith("2011-10") and x.get("customer"):
        oct_[x["customer"]] = oct_.get(x["customer"], 0) + x["gross_minor"] - x["discount_minor"]
lapsed = [x["customer"] for x in c["lapsed"]]
all_lapsed = c["lapsed_count"]
came_back = [cu for cu in lapsed if cu in oct_]
print(f"of the top {len(lapsed)} lapsed (by past spend), {len(came_back)} ordered in October WITHOUT any message "
      f"({len(came_back) / len(lapsed):.0%}), spending GBP {sum(oct_[cu] for cu in came_back) / 100:,.0f}")
active = [x for x in c.get("lapsed", [])]
for b in r["bundles"][:3]:
    print("bundle:", b["text"])
full = opportunities.compute(recs, "GBP", "en", as_of="2011-09-30")["customers"]
print("preliminary:", full["preliminary"], "|", full["preliminary_reason"])
res = full["result"]
print({k: res[k] for k in ("message_group", "compare_group", "return_rate_message", "return_rate_compare",
                           "incremental_revenue")})
print(res["text"])
