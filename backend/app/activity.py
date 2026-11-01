"""Activity: what the models did and what the rules and people decided, in one place.

Model calls are recorded as METADATA only (job, model, worked or not, how long). Prompts, photos and answers are
never stored here. The audit trail comes from audit_log, which every guarded step already writes to."""
import os

from . import llm
from .db import get_conn, init_db

RECENT = 40


def record(job, model, ok, ms, note=""):
    """The llm observer. Opens its own short connection, and must never break a request."""
    conn = get_conn(os.environ.get("KONSYER_DB", "konsyer.db"))
    try:
        init_db(conn)
        conn.execute("INSERT INTO model_calls(job, model, ok, ms, note) VALUES (?,?,?,?,?)",
                     (job if job in llm.JOBS else "agent", str(model)[:80], 1 if ok else 0, max(0, int(ms)), note))
        conn.commit()
    finally:
        conn.close()


def install():
    llm.set_observer(record)


PERSON = {"approved_by_user", "denied_by_user", "merchant_verified", "verified", "dispute_refunded", "dispute_rejected"}
BLOCKED = {"policy_deny", "approval_blocked", "hold_blocked", "execution_failed"}
MODEL = {"dispute_assessed", "contact_drafted", "proposed", "listing_created_from_flyer"}


def _outcome(rule, event):
    """Who acted on this step: a person decided, a rule blocked it, the model proposed or recommended, or plain
    code allowed and recorded it."""
    rule, event = rule or "", event or ""
    if event in PERSON or rule in ("human_decides", "user_declined"):
        return "person"
    if event in BLOCKED:
        return "rule_blocked"
    if event in MODEL or "recommendation" in rule:
        return "model"
    return "rule"


def view(conn, user_id, limit=RECENT) -> dict:
    limit = max(1, min(int(limit), 100))
    calls = [dict(r) for r in conn.execute(
        "SELECT id, job, model, ok, ms, note, created_at FROM model_calls ORDER BY id DESC LIMIT ?", (limit,))]
    for c in calls:
        c["ok"] = bool(c["ok"])
        c["what"] = llm.JOBS.get(c["job"], c["job"])
    totals = [dict(r) for r in conn.execute(
        "SELECT job, COUNT(*) AS calls, SUM(ok) AS worked, CAST(AVG(ms) AS INTEGER) AS avg_ms "
        "FROM model_calls GROUP BY job ORDER BY calls DESC")]
    for t in totals:
        t["what"] = llm.JOBS.get(t["job"], t["job"])
        t["worked"] = int(t["worked"] or 0)
    audit = []
    for r in conn.execute(
            "SELECT id, event, why, rule, created_at FROM audit_log WHERE user_id=? OR user_id IS NULL "
            "ORDER BY id DESC LIMIT ?", (user_id, limit)):
        a = dict(r)
        a["actor"] = _outcome(a["rule"], a["event"])
        audit.append(a)
    return {"model_calls": calls, "model_totals": totals, "audit": audit,
            "note": "Model calls are metadata only: no prompts, photos or answers are stored."}
