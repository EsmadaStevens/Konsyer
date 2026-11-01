"""Web-lead helpers. Plain code, no model.

 * The lead CARD is drawn from stored data, so prices always appear exactly as the page worded them
   (shown as a hint, never as a fact) whatever the model says in its reply.
 * A contact draft can be made with one tap, without calling the agent.
 * "Did they reply, is it still open?" reports from users. They change labels and visibility only: a lead
   is never marked verified, and one user's report only hides it for that user. A lead disappears for
   everyone only when at least 2 different users reported it closed and more said closed than open.
"""
import json
import os
import re
from datetime import datetime, timezone

from . import contact, discovery

STATUSES = {"open", "no_reply", "closed"}
PRICES = {"matched", "differs"}
STALE_MIN_REPORTS = 2
MAX_CARD_LEADS = 8
STATUS = {"not_found": 404, "invalid": 400}

_AMOUNT = re.compile(r"K\s?\d[\d,]*(?:\.\d+)?", re.I)


class LeadError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def show_phones() -> bool:
    """DISCOVERY_SHOW_PHONES=0 hides web-lead phone numbers (and the WhatsApp/call buttons)."""
    return os.environ.get("DISCOVERY_SHOW_PHONES", "1") != "0"


def short_price(text, n=110) -> str:
    """A page's price text can be a whole social post. Keep a window around the first K amount."""
    t = contact.one_line(text, 2000)
    if len(t) <= n:
        return t
    m = _AMOUNT.search(t)
    if not m:
        return t[:n].rstrip() + "..."
    start, end = max(0, m.start() - n // 2), min(len(t), m.end() + n // 2)
    return ("..." if start else "") + t[start:end].strip() + ("..." if end < len(t) else "")


def lead_key(phone, source_url) -> str:
    p = contact.parse_zm_phone(phone)
    return "p:" + p if p else "u:" + str(source_url or "").lower()[:200]


def _key_of(row) -> str:
    return lead_key(row["phone"], row["source_url"])


# ---------- reports ----------
def summary(conn, key) -> dict:
    s = {"open": 0, "no_reply": 0, "closed": 0, "price_matched": 0, "price_differs": 0}
    for r in conn.execute("SELECT status, price FROM lead_feedback WHERE lead_key=?", (key,)):
        if r["status"] in STATUSES:
            s[r["status"]] += 1
        if r["price"] == "matched":
            s["price_matched"] += 1
        elif r["price"] == "differs":
            s["price_differs"] += 1
    return s


def _mine(conn, user_id, key) -> dict:
    r = conn.execute("SELECT status, price FROM lead_feedback WHERE user_id=? AND lead_key=?",
                     (user_id, key)).fetchone()
    return {"status": r["status"], "price": r["price"]} if r else {"status": None, "price": None}


def _stale(conn, user_id, key) -> bool:
    if _mine(conn, user_id, key)["status"] == "closed":
        return True
    s = summary(conn, key)
    return s["closed"] >= STALE_MIN_REPORTS and s["closed"] > s["open"]


def visible(conn, user_id, rows) -> list:
    """Drop leads this user reported closed, or that several users reported closed."""
    return [r for r in rows if not _stale(conn, user_id, _key_of(r))]


def record_feedback(conn, user_id, lead_id, status=None, price=None) -> dict:
    if status is not None and status not in STATUSES:
        raise LeadError("invalid", "Status must be one of: " + ", ".join(sorted(STATUSES)) + ".")
    if price is not None and price not in PRICES:
        raise LeadError("invalid", "Price answer must be 'matched' or 'differs'.")
    if status is None and price is None:
        raise LeadError("invalid", "Say whether they replied, or whether the price matched.")
    lead = _get(conn, lead_id)
    key = _key_of(lead)
    conn.execute(
        "INSERT INTO lead_feedback(user_id, lead_key, lead_name, status, price, updated_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(user_id, lead_key) DO UPDATE SET status=COALESCE(excluded.status, status), "
        "price=COALESCE(excluded.price, price), updated_at=excluded.updated_at",
        (user_id, key, contact.one_line(lead["name"], 80), status, price,
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.execute("INSERT INTO audit_log(user_id, event, why, rule) VALUES (?,?,?,?)",
                 (user_id, "lead_feedback", f"You told us about '{contact.one_line(lead['name'], 60)}': "
                  f"{status or ''} {price or ''}".strip(), "user_report"))
    conn.commit()
    return {"ok": True, "signals": summary(conn, key), "mine": _mine(conn, user_id, key),
            "hidden": _stale(conn, user_id, key)}


# ---------- reading ----------
def _get(conn, lead_id):
    try:
        row = discovery.get_lead(conn, int(lead_id))
    except (TypeError, ValueError):
        row = None
    if row is None:
        raise LeadError("not_found", "That lead is no longer available (leads last 24 hours). Search again.")
    return row


def view(conn, row, user_id) -> dict:
    phone = contact.parse_zm_phone(row["phone"]) if show_phones() else None
    key = _key_of(row)
    return {"lead_id": row["id"], "name": row["name"], "area": row["area"], "platform": row["platform"],
            "source_host": contact.host_of(row["source_url"]),
            "price_text": short_price(row["price_text"]) or None,
            "price_for": contact.one_line(row["price_for"], 80) or None,
            "phone": phone, "has_whatsapp": contact.phone_kind(phone) == "mobile",
            "corroborated": bool(row["corroborated"]), "sample_data": bool(row["is_sample"]),
            "signals": summary(conn, key), "mine": _mine(conn, user_id, key)}


def ids_from_trace(trace) -> list:
    """Lead ids the agent was shown this turn (from its discover_services results), in order, no repeats."""
    out = []
    for t in trace or []:
        res = t.get("result") if isinstance(t, dict) else None
        if t.get("tool") == "discover_services" and isinstance(res, dict):
            for lead in res.get("leads") or []:
                m = re.fullmatch(r"L(\d{1,9})", str(lead.get("lead_id", "")))
                if m and int(m.group(1)) not in out:
                    out.append(int(m.group(1)))
    return out


def mentioned_ids(conn, user_id, reply) -> list:
    """Leads this user was last shown whose name appears in the model's reply. The model sometimes answers from the
    remembered leads without searching again; the card must still appear."""
    text = (reply or "").lower()
    r = conn.execute("SELECT lead_ids FROM recent_leads WHERE user_id=?", (user_id,)).fetchone()
    if not r or not text:
        return []
    out = []
    for i in json.loads(r["lead_ids"]):
        row = conn.execute("SELECT name FROM web_leads WHERE id=?", (i,)).fetchone()
        if row and row["name"] and row["name"].lower() in text:
            out.append(i)
    return out


def card(conn, user_id, lead_ids):
    views = []
    for i in lead_ids[:MAX_CARD_LEADS]:
        try:
            row = _get(conn, i)
        except LeadError:
            continue
        if _stale(conn, user_id, _key_of(row)):  # you reported it closed
            continue
        views.append(view(conn, row, user_id))
    return {"type": "leads", "title": "Web leads (unverified)", "leads": views} if views else None


def draft_contact(conn, user_id, lead_id, details) -> dict:
    """A contact draft built by code from a template. Nothing is sent: the user taps send."""
    lead = _get(conn, lead_id)
    phone = contact.parse_zm_phone(lead["phone"]) if show_phones() else None
    url = lead["source_url"]
    message = contact.build_message(lead["name"], details)
    chans = contact.channels(phone, url, message)
    if not chans:
        raise LeadError("invalid", "There are no contact details for this seller. Ask them directly.")
    conn.execute("INSERT INTO audit_log(user_id, event, why, rule) VALUES (?,?,?,?)",
                 (user_id, "contact_drafted",
                  f"Drafted a message to '{contact.one_line(lead['name'], 60)}'. Nothing was sent: the user taps "
                  "send in their own app.", "draft_only"))
    conn.commit()
    return {"name": lead["name"], "message": message, "channels": chans, "phone": phone, "source_url": url,
            "price_text": short_price(lead["price_text"]) or None,
            "price_for": contact.one_line(lead["price_for"], 80) or None,
            "corroborated": bool(lead["corroborated"]), "verified": False, "sample_data": bool(lead["is_sample"])}