"""Drive Ribhiya's real HTTP API in-process (FastAPI TestClient) on a set of files and dump everything.

usage: uv run --project ~/revenue-agent python -I run_ribhiya.py config.json
config: {"name", "db_dir", "uploads": [[path, ...], ...] (each inner list = one upload batch),
         "answers": [{"qid", "status", "value"}], "scenarios": [{"label", "period", "scenario"}],
         "advisor_questions": [...], "out": "result.json"}
Never calls a paid model: all LLM keys are removed and Hermes is disabled, so the advisor runs in rules mode.
"""
import json
import os
import sys
import time
from pathlib import Path

cfg = json.loads(Path(sys.argv[1]).read_text())
db_dir = Path(cfg["db_dir"])
db_dir.mkdir(parents=True, exist_ok=True)
for f in db_dir.glob("*.db*"):
    f.unlink()
os.environ.update(DATA_DIR=str(db_dir), DB_PATH=str(db_dir / "eval.db"), ENGINE="offline", HERMES_SKILL_SYNC="0")
for k in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "NVIDIA_API_KEY", "ANTHROPIC_API_KEY", "TENANT_TOKENS",
          "ADMIN_TOKEN"):
    os.environ[k] = ""  # empty, not removed: config.py loads .env with setdefault, so this blocks the real keys
if os.environ.get("ALLOW_FREE_LLM") == "1":  # opt-in: let .env's free Gemini key through for the advisor
    for k in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "NVIDIA_API_KEY"):
        os.environ.pop(k)

from fastapi.testclient import TestClient  # noqa: E402
from revenue_agent.profit import advisor  # noqa: E402
from revenue_agent.web.app import app  # noqa: E402

advisor.hermes_ready = lambda tenant="default": False
c = TestClient(app)
log = {"name": cfg["name"], "timings_s": {}, "uploads": [], "scenarios": [], "advisor": []}


def timed(key, fn):
    t = time.perf_counter()
    r = fn()
    log["timings_s"][key] = round(time.perf_counter() - t, 3)
    return r


iid = c.post("/api/investigations", json={"title": cfg["name"]}).json()["id"]
if cfg.get("currency"):  # the owner sets the business currency (sales files often have no currency column)
    log["currency_set"] = c.put(f"/api/investigations/{iid}/currency", json={"currency": cfg["currency"]}).status_code
for a in cfg.get("answers", []):
    r = c.post(f"/api/investigations/{iid}/answers", json=a)
    if r.status_code != 200:
        log.setdefault("answer_errors", []).append([a, r.status_code, r.text[:300]])
for i, batch in enumerate(cfg["uploads"]):
    form = {}
    if isinstance(batch, dict):  # {"files": [...], "form": {...}}: owner-confirmed type/mapping, like the UI's MappingFix
        form = {k: json.dumps(v) if isinstance(v, dict) else str(v).lower() if isinstance(v, bool) else v
                for k, v in batch["form"].items()}
        batch = batch["files"]
    files = [("files", (Path(p).name, Path(p).read_bytes(), "application/octet-stream")) for p in batch]
    r = timed(f"upload_{i}", lambda: c.post(f"/api/investigations/{iid}/files", files=files, data=form))
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text[:500]}
    log["uploads"].append({"status_code": r.status_code, "files": [Path(p).name for p in batch], "body": body})
for a in cfg.get("answers_after", []):
    c.post(f"/api/investigations/{iid}/answers", json=a)
st = timed("state_en", lambda: c.get(f"/api/investigations/{iid}?lang=en").json())
timed("state_ar", lambda: c.get(f"/api/investigations/{iid}?lang=ar"))
for s in cfg.get("scenarios", []):
    r = timed(f"sim_{s['label']}", lambda: c.post(f"/api/investigations/{iid}/simulate",
                                                 json={"period": s.get("period"), "scenario": s["scenario"],
                                                       "lang": "en"}))
    log["scenarios"].append({"label": s["label"], "status_code": r.status_code, "body": r.json()})
for q in cfg.get("advisor_questions", []):
    r = c.post(f"/api/investigations/{iid}/advisor", json={"question": q, "lang": "en"})
    log["advisor"].append({"q": q, "status_code": r.status_code, "body": r.json()})
log["state"] = st
Path(cfg["out"]).write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str))
d = st.get("diagnosis") or {}
print(json.dumps({"stage": st["stage"], "record_counts": st["record_counts"], "timings": log["timings_s"],
                  "periods": [(p["period"], p["orders"], p["net_sales"], p["contribution"]) for p in
                              st["metrics"]["periods"]],
                  "findings": [(f["driver"], f["change_per_order"], f["confidence"]) for f in d.get("findings", [])],
                  "untested": d.get("untested"), "gaps": d.get("gaps"),
                  "upload_status": [[(x.get("filename") or x.get("name"), x.get("status")) for x in
                                     u["body"].get("results", [])] for u in log["uploads"]]},
                 ensure_ascii=False, indent=1, default=str))
