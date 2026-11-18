"""Ribhiya MCP server for agent platforms (Wesam.ai): workflow, business isolation by token, masking. Test-only data."""

import base64
import io
from types import SimpleNamespace

import pytest

from revenue_agent import ribhiya_mcp as rm


def ctx(token: str | None):
    headers = {"authorization": f"Bearer {token}"} if token else {}
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers=headers)))


@pytest.fixture(autouse=True)
def tokens(monkeypatch):
    monkeypatch.setenv("TENANT_TOKENS", "shop1:shop1-token-123456,shop2:shop2-token-123456")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)


def _sales() -> str:
    out = io.StringIO()
    out.write("Order ID,Date,Product,Quantity,Unit Price,Customer\n")
    for m, price in (("2026-08", 100), ("2026-09", 80)):            # like-for-like price cut in September
        for i in range(40):
            out.write(f"{m}-{i},{m}-{i % 27 + 1:02d},Mug,2,{price},Customer Number {i % 25}\n")
    return out.getvalue()


def test_full_workflow_over_tools_with_compact_outputs():
    A = ctx("shop1-token-123456")
    iid = rm.ribhiya_start("Mug shop", ctx=A)["investigation_id"]
    r = rm.ribhiya_add_file(iid, "sales.csv", content_text=_sales(), ctx=A)
    assert r["status"] == "imported" and r["tables"][0]["type"] == "sales"
    costs = base64.b64encode(b"Product,Unit Cost\nMug,40\n").decode()
    assert rm.ribhiya_add_file(iid, "costs.csv", content_base64=costs, ctx=A)["status"] == "imported"
    f = rm.ribhiya_findings(iid, lang="en", ctx=A)
    assert f["findings"][0]["driver"] == "price_and_mix" and "sell the same products for less" in f["findings"][0]["what"]
    assert len(str(f)) < 6000                                          # small enough for a platform model
    w = rm.ribhiya_what_if(iid, {"price_change_pct": {"all": 10}}, lang="en", ctx=A)
    assert w["type"] == "projection" and w["profit_change"].startswith("EGP")
    g = rm.ribhiya_grow(iid, lang="en", ctx=A)
    assert g["customers"]["repeat_rate"] > 0
    assert all("…" in x["customer"] for x in g["customers"]["message_now"])   # names masked by default


def test_the_token_decides_the_business_and_bad_tokens_are_refused():
    iid = rm.ribhiya_start("A", ctx=ctx("shop1-token-123456"))["investigation_id"]
    assert rm.ribhiya_findings(iid, ctx=ctx("shop2-token-123456"))["code"] == "not_found"
    assert rm.ribhiya_findings(iid, ctx=ctx("wrong-token-0000000"))["code"] == "unauthorized"
    assert rm.ribhiya_start("x", ctx=ctx(None))["code"] == "unauthorized"


def test_http_refused_without_tokens(monkeypatch):
    monkeypatch.delenv("TENANT_TOKENS")
    assert rm.ribhiya_start("x", ctx=ctx("anything-123456"))["code"] == "unauthorized"


def test_no_tool_can_send_approve_delete_or_spend():
    names = {t.name for t in rm.mcp._tool_manager.list_tools()}
    assert names == {"ribhiya_start", "ribhiya_upload_link", "ribhiya_add_file", "ribhiya_add_file_from_url",
                     "ribhiya_findings", "ribhiya_what_if", "ribhiya_grow", "ribhiya_definitions"}


def test_upload_link_puts_files_in_the_right_business_without_the_model(monkeypatch):
    from fastapi.testclient import TestClient
    from revenue_agent.web.app import app
    A = ctx("shop1-token-123456")
    iid = rm.ribhiya_start("Mug shop", ctx=A)["investigation_id"]
    link = rm.ribhiya_upload_link(iid, ctx=A)
    path = link["url"].split("127.0.0.1:8080")[-1]
    assert rm.ribhiya_upload_link(iid, ctx=ctx("shop2-token-123456"))["code"] == "not_found"
    c = TestClient(app)
    assert "ارفع" in c.get(path).text
    r = c.post(path + "/files", files=[("files", ("sales.csv", _sales().encode(), "text/csv"))])
    assert "اتقرا ✓" in r.text
    assert rm.ribhiya_findings(iid, lang="en", ctx=A)["stage"] != "documents"
    bad = path[:-3] + ("aaa" if not path.endswith("aaa") else "bbb")
    assert c.get(bad).status_code == 404                                   # tampered signature
    from revenue_agent.web import upload_link
    tok, _ = upload_link.make("shop1", iid, ttl_s=-1)
    assert c.post(f"/u/{tok}/files", files=[("files", ("s.csv", b"x", "text/csv"))]).status_code == 404  # expired


def test_file_urls_only_from_allowlisted_https_hosts(monkeypatch):
    A = ctx("shop1-token-123456")
    iid = rm.ribhiya_start("A", ctx=A)["investigation_id"]
    for url in ("http://files.wesam.test/a.csv", "https://169.254.169.254/latest", "https://evil.test/a.csv",
                "file:///etc/passwd"):
        assert rm.ribhiya_add_file_from_url(iid, url, ctx=A)["code"] == "invalid"
    monkeypatch.setenv("RIBHIYA_FILE_HOSTS", "files.wesam.test")
    import httpx

    class Resp:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_bytes(self): yield _sales().encode()
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Resp())
    assert rm.ribhiya_add_file_from_url(iid, "https://files.wesam.test/sales.csv", ctx=A)["status"] == "imported"
