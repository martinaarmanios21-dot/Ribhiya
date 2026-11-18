"""Signed upload links: files go straight into an investigation without passing through an AI model.

An agent platform (Wesam.ai, Hermes...) asks Ribhiya for a link (ribhiya_upload_link, ~50 tokens) and gives it to the
owner, who uploads the exports in the browser. Sending a file through a model as base64 would cost ~15,000 output
tokens per month of sales; this costs none. The link is bound to one business and one investigation, signed with a
server secret, and expires (default 24 h). It can only ADD files to that investigation: it reads nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import time

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from .. import db
from ..profit import ingest, store

router = APIRouter()
LINK_TTL_S = 24 * 3600


def _secret() -> bytes:
    env = os.environ.get("RIBHIYA_LINK_SECRET", "").strip()
    if env:
        return env.encode()
    conn = db.connect()
    s = db.kv_get(conn, "upload_link_secret")
    if not s:
        s = secrets.token_hex(32)
        db.kv_set(conn, "upload_link_secret", s)
    return str(s).encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def make(tenant: str, iid: str, ttl_s: int = LINK_TTL_S) -> tuple[str, int]:
    exp = int(time.time()) + ttl_s
    body = _b64(json.dumps({"t": tenant, "i": iid, "e": exp}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", exp


def verify(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        good = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    return data if data.get("e", 0) > time.time() else None


def public_url(token: str) -> str:
    base = os.environ.get("RIBHIYA_PUBLIC_URL", "http://127.0.0.1:8080").rstrip("/")
    return f"{base}/u/{token}"


_PAGE = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ربحية · ارفع ملفاتك</title>
<style>body{{font-family:system-ui,'IBM Plex Sans Arabic',sans-serif;background:#f6f7f5;color:#13261f;margin:0}}
main{{max-width:560px;margin:8vh auto;background:#fff;border-radius:18px;padding:28px;box-shadow:0 8px 30px #0001}}
h1{{font-size:22px;margin:0 0 6px}}p{{color:#4b5d55;line-height:1.7}}input[type=file]{{margin:14px 0;width:100%}}
button{{background:#145c43;color:#fff;border:0;border-radius:12px;padding:12px 20px;font-size:16px;cursor:pointer}}
li{{margin:6px 0}}.ok{{color:#145c43}}.bad{{color:#b3261e}}small{{color:#6b7c74}}</style></head><body><main>
<h1>ربحية · ارفع ملفاتك</h1>{body}<p><small>Ribhiya · secure upload link for “{title}”. Files go only to this
investigation. Expires {exp}.</small></p></main></body></html>"""

_FORM = """<p>ارفع تقرير المبيعات، وتكلفة المنتجات، والفواتير (CSV أو Excel أو PDF). ملفاتك بتروح للتحليل ده بس.</p>
<form method="post" action="/u/{token}/files" enctype="multipart/form-data">
<input type="file" name="files" multiple required accept=".csv,.txt,.xlsx,.xlsm,.pdf"><br>
<button type="submit">ارفع وحلّل</button></form>"""


def _expired_page() -> HTMLResponse:
    return HTMLResponse(_PAGE.format(body="<p class='bad'>اللينك ده انتهى أو مش صحيح. اطلب لينك جديد.</p>",
                                     title="—", exp="—"), status_code=404)


@router.get("/u/{token}", response_class=HTMLResponse)
def upload_page(token: str):
    data = verify(token)
    if not data:
        return _expired_page()
    try:
        inv = store.get(db.connect(), data["i"], data["t"])
    except store.NotFound:
        return _expired_page()
    exp = time.strftime("%Y-%m-%d %H:%M", time.localtime(data["e"]))
    return HTMLResponse(_PAGE.format(body=_FORM.format(token=html.escape(token)), title=html.escape(inv["title"]),
                                     exp=exp))


@router.post("/u/{token}/files", response_class=HTMLResponse)
async def upload_files(token: str, files: list[UploadFile] = File(...)):
    data = verify(token)
    if not data:
        return _expired_page()
    conn = db.connect()
    try:
        inv = store.get(conn, data["i"], data["t"])
    except store.NotFound:
        return _expired_page()
    rows = []
    for f in files[:10]:
        raw = await f.read(ingest.MAX_BYTES + 1)
        r = await run_in_threadpool(store.add_file, conn, data["i"], f.filename or "upload", raw, tenant=data["t"],
                                    actor="upload_link")
        ok = r["status"] == "imported"
        why = "" if ok else html.escape(r["summary"].get("fatal_ar") or r["summary"].get("fatal") or "")
        rows.append(f"<li class='{'ok' if ok else 'bad'}'>{html.escape(f.filename or '')}: "
                    f"{'اتقرا ✓' if ok else 'ماتقراش'} {why}</li>")
    body = ("<p>تمام، الملفات وصلت. ارجع للمحادثة واطلب التحليل.</p><ul>" + "".join(rows) + "</ul>" +
            _FORM.format(token=html.escape(token)))
    exp = time.strftime("%Y-%m-%d %H:%M", time.localtime(data["e"]))
    return HTMLResponse(_PAGE.format(body=body, title=html.escape(inv["title"]), exp=exp))
