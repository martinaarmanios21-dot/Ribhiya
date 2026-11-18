"""make_cfg.py name files_glob_root out_cfg [batches=month|all]"""
import glob, json, sys
from pathlib import Path
name, root, out = sys.argv[1], Path(sys.argv[2]).resolve(), sys.argv[3]
batches = []
extra = sorted(glob.glob(str(root / "*.csv")))  # e.g. product_costs.csv at the root
for m in sorted(p for p in root.iterdir() if p.is_dir()):
    batches.append(sorted(glob.glob(str(m / "*"))))
if extra:
    batches.insert(0, extra)
ans = [{"qid": "p_sells", "status": "answered", "value": "products"},
       {"qid": "p_worry", "status": "answered", "value": "profit_low_despite_sales"},
       {"qid": "p_channels", "status": "answered", "value": ["website", "marketplaces"]}]
last = sorted(p.name for p in root.iterdir() if p.is_dir())[-1] if any(p.is_dir() for p in root.iterdir()) else None
scen = [{"label": "price+5", "period": last, "scenario": {"price_change_pct": {"all": 5}}},
        {"label": "price+5_vol-10", "period": last, "scenario": {"price_change_pct": {"all": 5}, "volume_change_pct": -10}},
        {"label": "no_discounts", "period": last, "scenario": {"discount_pct": 0}},
        {"label": "returns_1pct", "period": last, "scenario": {"return_rate_pct": 1}}]
json.dump({"name": name, "db_dir": f"/Users/rj/ribhiya-eval/out/db_{name}", "uploads": batches, "answers": ans,
           "scenarios": scen, "advisor_questions": ["Why is my profit down?", "What should I do now?"],
           "out": f"/Users/rj/ribhiya-eval/out/{name}.json"}, open(out, "w"), indent=1)
