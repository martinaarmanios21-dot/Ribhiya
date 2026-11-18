"""Per-business consent for AI providers.

When Ribhiya hosts many businesses (TENANT_TOKENS set), no business data goes to an external AI provider until that
business switches AI on: free-tier providers may log prompts or use them for training, and Egypt's data protection
law (151/2020) restricts sending data abroad without consent. Answers still work without AI (rule-based, from the
same calculations). A self-hosted single business (no TENANT_TOKENS) is on by default because it uses its own key.
PROFIT_AI_DEFAULT=on|off overrides the default for businesses that have not chosen.
"""

from __future__ import annotations

import os
import sqlite3
import time

KEY = "ai_enabled"


def default() -> bool:
    forced = os.environ.get("PROFIT_AI_DEFAULT", "").strip().lower()
    if forced in ("on", "off"):
        return forced == "on"
    return not os.environ.get("TENANT_TOKENS", "").strip()


def enabled(conn: sqlite3.Connection, tenant: str) -> bool:
    row = conn.execute("SELECT value FROM tenant_settings WHERE tenant_id=? AND key=?", (tenant, KEY)).fetchone()
    return default() if row is None else row[0] == "1"


def set_enabled(conn: sqlite3.Connection, tenant: str, on: bool, actor: str) -> dict:
    from .. import db
    from ..ledger.store import audit
    with db.tx(conn):
        conn.execute("INSERT INTO tenant_settings(tenant_id, key, value, updated_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(tenant_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                     (tenant, KEY, "1" if on else "0", time.time()))
        audit(conn, actor, "ai_setting", tenant=tenant, enabled=on)
    return status(conn, tenant)


def status(conn: sqlite3.Connection, tenant: str) -> dict:
    from ..llm import tenant_month_usage
    return {"ai_enabled": enabled(conn, tenant), "default": default(), "month_usage": tenant_month_usage(conn, tenant),
            "sent_when_on": ["monthly totals and margins", "product names with their profit", "findings and plans",
                             "your question"],
            "never_sent": ["customer names, phones or emails", "raw uploaded files", "supplier email addresses"],
            "sent_when_on_ar": ["إجماليات الشهور والهوامش", "أسماء المنتجات وربحها", "اللي لقيناه والخطط", "سؤالك"],
            "never_sent_ar": ["أسماء العملاء وتليفوناتهم وإيميلاتهم", "الملفات اللي رفعتها نفسها", "إيميلات الموردين"]}
