"""Hardening for paid multi-business hosting (findings of the 2026-10-29 security audit). Test-only data."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from revenue_agent.web.app import app
    for k in ("ADMIN_TOKEN", "TENANT_TOKENS"):
        monkeypatch.delenv(k, raising=False)
    return TestClient(app)


@pytest.mark.parametrize("env", [{"TENANT_TOKENS": "acme:short"}, {"TENANT_TOKENS": "Bad Id!:long-enough-token-1"},
                                 {"ADMIN_TOKEN": "too-short-admin"},
                                 {"TENANT_TOKENS": "a:same-token-123456,b:same-token-123456"}])
def test_broken_token_settings_fail_closed_instead_of_opening_the_api(client, monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    r = client.get("/api/investigations")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "auth_misconfigured"


def test_open_local_mode_only_answers_localhost(client):
    assert client.get("/api/investigations").status_code == 200
    assert client.get("/api/investigations", headers={"host": "evil.example"}).status_code == 401


def test_other_websites_cannot_call_the_api_from_the_owners_browser(client):
    r = client.post("/api/investigations", json={"title": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert "access-control-allow-origin" not in client.get("/api/investigations",
                                                           headers={"origin": "https://evil.example"}).headers
    assert client.post("/api/investigations", json={"title": "x"},
                       headers={"origin": "http://testserver"}).status_code == 200   # the app itself


def test_xlsx_zip_bomb_is_refused_before_opening():
    import io
    import zipfile
    from revenue_agent.profit import ingest
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", "<x>" + "0" * 30_000_000 + "</x>")   # 30 MB -> a few KB
    fr = ingest.parse_file("bomb.xlsx", buf.getvalue())
    assert fr.fatal and "expands" in fr.fatal and "احفظه CSV" in fr.summary()["fatal_ar"]


def test_database_file_is_owner_only_and_secure_delete_is_on(tmp_path, monkeypatch):
    from revenue_agent import db
    path = tmp_path / "x.db"
    conn = db.connect(path)
    conn.close()
    conn = db.connect(path)
    assert path.stat().st_mode & 0o077 == 0
    assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1


def _two_business_client(monkeypatch):
    from revenue_agent.web.app import app
    monkeypatch.setenv("TENANT_TOKENS", "acme:acme-token-123456,beta:beta-token-123456")
    return TestClient(app), {"X-Tahsila-Token": "acme-token-123456"}, {"X-Tahsila-Token": "beta-token-123456"}


SALES = b"Order ID,Date,Product,Quantity,Unit Price,Customer\nA,2026-09-01,Mug,1,100,Mona\n"


def test_a_business_can_erase_all_its_data_and_only_its_data(monkeypatch):
    c, A, B = _two_business_client(monkeypatch)
    ia = c.post("/api/investigations", headers=A, json={"title": "A"}).json()["id"]
    ib = c.post("/api/investigations", headers=B, json={"title": "B"}).json()["id"]
    for iid, h in ((ia, A), (ib, B)):
        c.post(f"/api/investigations/{iid}/files", headers=h, files=[("files", ("s.csv", SALES, "text/csv"))])
    c.put("/api/profit/ai-settings", headers=A, json={"ai_enabled": True})
    assert c.request("DELETE", "/api/profit/my-data", headers=A, json={}).status_code == 422
    r = c.request("DELETE", "/api/profit/my-data", headers=A, json={"confirm": "ERASE"}).json()["erased"]
    assert r["investigations"] == 1 and r["inv_records"] == 1 and r["tenant_settings"] == 1
    assert c.get("/api/investigations", headers=A).json() == []
    assert c.get("/api/profit/ai-settings", headers=A).json()["ai_enabled"] is False
    assert len(c.get("/api/investigations", headers=B).json()) == 1          # the other business is untouched
    assert c.get(f"/api/investigations/{ib}", headers=B).json()["record_counts"]["sales"] == 1


def test_failed_replacement_keeps_the_old_file_and_same_file_can_be_remapped(monkeypatch):
    c, A, _ = _two_business_client(monkeypatch)
    iid = c.post("/api/investigations", headers=A, json={"title": "A"}).json()["id"]
    up = c.post(f"/api/investigations/{iid}/files", headers=A, files=[("files", ("s.csv", SALES, "text/csv"))]).json()
    fid = up["results"][0]["file_id"]
    bad = c.post(f"/api/investigations/{iid}/files", headers=A, data={"replace_file_id": str(fid)},
                 files=[("files", ("x.docx", b"nope", "application/octet-stream"))]).json()
    assert bad["results"][0]["status"] == "failed"
    assert c.get(f"/api/investigations/{iid}", headers=A).json()["record_counts"]["sales"] == 1   # old kept
    same = c.post(f"/api/investigations/{iid}/files", headers=A, data={"replace_file_id": str(fid)},
                  files=[("files", ("s.csv", SALES, "text/csv"))]).json()
    assert same["results"][0]["status"] == "imported"
    st = c.get(f"/api/investigations/{iid}", headers=A).json()
    assert st["record_counts"]["sales"] == 1 and [f["status"] for f in st["files"]].count("imported") == 1


def test_security_headers_and_strict_csp_on_the_app(client):
    r = client.get("/")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "'unsafe-inline'" not in csp.split("style-src")[0]
    assert "x-frame-options" in client.get("/api/investigations").headers


def test_oversized_requests_are_refused_before_reading(client):
    r = client.post("/api/investigations/x/files", headers={"content-length": "999999999"}, content=b"")
    assert r.status_code == 413


def test_audit_identity_comes_from_the_token_not_a_header(monkeypatch):
    from revenue_agent import db
    c, A, _ = _two_business_client(monkeypatch)
    c.post("/api/investigations", headers=A | {"X-Tahsila-User": "admin:default"}, json={"title": "A"})
    row = db.connect().execute("SELECT actor FROM audit_log WHERE action='investigation_created' "
                               "ORDER BY id DESC").fetchone()
    assert row[0].startswith("member:acme")


def test_csp_hashes_match_what_the_browser_executes(tmp_path, monkeypatch):
    """The router's hydration script contains a NUL byte; browsers hash it as U+FFFD (found with headless Chrome)."""
    import base64
    import hashlib
    from revenue_agent.web import app as app_mod
    (tmp_path / "index.html").write_text("<html><script>a='x\x00y';\r\nb=1</script><script src='/m.js'></script></html>",
                                         encoding="utf-8")
    monkeypatch.setattr(app_mod, "FRONTEND_DIST", tmp_path)
    browser_text = "a='x�y';\nb=1"
    want = base64.b64encode(hashlib.sha256(browser_text.encode()).digest()).decode()
    assert f"'sha256-{want}'" in app_mod._csp()
