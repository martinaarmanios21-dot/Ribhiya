# Real-data evaluation (UCI Online Retail II)

Reproduces the reliability check behind the README's "Checked on real data". The data is a real UK online retailer's
transactions (UCI Online Retail II, CC BY 4.0); it is not committed. Only product unit costs are assumed
(50% of each product's median price), because the source has none.

```bash
mkdir -p ~/ribhiya-eval/raw && cd ~/ribhiya-eval/raw
# Aug–Oct 2011 is roughly bytes 71.5M–85.5M of the CSV mirror (a byte range keeps the download ~14 MB)
curl -L -r 71500000-85499999 -o slice.csv \
  https://huggingface.co/datasets/attik/Online-Retail-II-UCI/resolve/main/online_retail_II.csv
cd ~/revenue-agent/evals/real_data
python3 -I prepare.py ~/ribhiya-eval/raw/slice.csv ~/ribhiya-eval/data/full        # raw/ and prepared/ files
python3 -I prepare.py ~/ribhiya-eval/raw/slice.csv ~/ribhiya-eval/data/small 0.1   # a small-shop sample
python3 -I make_cfg.py full_prepared ~/ribhiya-eval/data/full/prepared ~/ribhiya-eval/cfg.json
(cd ~/revenue-agent && uv run python -I evals/real_data/run_ribhiya.py ~/ribhiya-eval/cfg.json)
python3 -I reconcile.py ~/ribhiya-eval/data/full/prepared ~/ribhiya-eval/out/full_prepared.json --mirror
python3 -I check_price_effect.py ~/ribhiya-eval/data/full/prepared ~/ribhiya-eval/out/full_prepared.json
(cd ~/revenue-agent && uv run python -I evals/real_data/backtest_opportunities.py ~/ribhiya-eval/data/full/raw)
python3 -I impact.py
```

`run_ribhiya.py` drives the real HTTP API in-process with every AI key blanked (rules mode, no cost).
`make_cfg.py` writes outputs under `/Users/rj/ribhiya-eval/out`; edit the paths in it for another machine.
`reconcile.py` recomputes every monthly metric in pandas (a different code path) and diffs it with Ribhiya's.

Results on 2026-10-29 (after the fixes in `tests/test_real_data_fixes.py`):

| Check | Result |
|---|---|
| 30 monthly metrics × 3 months, 141k sales lines | identical to the independent calculation |
| Like-for-like price / cost / basket split | small shop identical; full data within 0.3 pence per order |
| Raw export, no manual preparation | imported automatically; money figures within ~1–2% of hand-cleaned files; same findings |
| Simulator "returns → 1%" | +GBP 30,580 (was −11,033 before the fix) |
| Win-back backtest | about half of "lapsed" customers came back unprompted; the measurement correctly reports no effect when nothing was sent |
| Speed | 60k-line month: ~1 s upload, ~2 s per page, ~4 s per what-if. Small shop: ~0.2 s |
