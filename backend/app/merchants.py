"""Merchant signup and verification. Plain code, no model.

A merchant registers and creates listings with real slots and capacity. Everything starts as status 'claimed':
the agent cannot see or propose a claimed listing. Only verify(), reached through the admin-key endpoint,
turns a merchant's claimed listings into 'verified'. Nothing a user types, and nothing a model reads, can verify."""
import json
import re
from datetime import date, datetime, timedelta

from .contact import is_http_url
from .flyers import MAX_PRICE, TYPES, normalize_phone
from .timeutil import LUSAKA, to_utc_iso

VERIFIED_TRUST = 0.8     # trust given to a verified merchant's listings (policy needs >= 0.5)
CLAIMED_TRUST = 0.3
MAX_SLOTS = 20
MAX_CAPACITY = 1000
STATUS = {"not_found": 404, "forbidden": 403, "invalid": 422}
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class MerchantError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _clean(text, n):
    return (text or "").strip()[:n] or None


def register(conn, user_id, f):
    if conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
        raise MerchantError("not_found", "User not found.")
    name = _clean(f.get("name"), 120)
    if not name:
        raise MerchantError("invalid", "Business name is required.")
    try:
        phone = normalize_phone(f.get("whatsapp"))
    except ValueError:
        raise MerchantError("invalid", "That WhatsApp number does not look right.")
    social = _clean(f.get("social_url"), 300)
    if social and not is_http_url(social):
        raise MerchantError("invalid", "The page link must start with https:// (for example your Facebook page).")
    mid = conn.execute(
        "INSERT INTO merchants(name, owner_user_id, whatsapp, area, social_url, verified, is_sample) "
        "VALUES (?,?,?,?,?,0,0)", (name, user_id, phone, _clean(f.get("area"), 80), social)).lastrowid
    conn.execute("INSERT INTO audit_log(user_id, event, why, rule) VALUES (?,?,?,?)",
                 (user_id, "merchant_registered",
                  f"'{name}' registered. It is not verified yet, so its listings are hidden from the agent.",
                  "merchant_signup"))
    conn.commit()
    return {"merchant_id": mid, "verified": False}


def _parse_slot(s, today, n):
    where = f"Slot {n}: "
    try:
        d = date.fromisoformat(str(s.get("date") or ""))
    except ValueError:
        raise MerchantError("invalid", where + "date must look like 2026-11-07.")
    if d < today:
        raise MerchantError("invalid", where + "that date is in the past.")
    st, en = str(s.get("start_time") or ""), s.get("end_time")
    if not _TIME.fullmatch(st):
        raise MerchantError("invalid", where + "start time must look like 18:30.")
    cap = s.get("capacity", 1)
    if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= MAX_CAPACITY:
        raise MerchantError("invalid", where + f"capacity must be a whole number from 1 to {MAX_CAPACITY}.")
    start = datetime.combine(d, datetime.strptime(st, "%H:%M").time(), tzinfo=LUSAKA)
    end = None
    if en not in (None, ""):
        if not _TIME.fullmatch(str(en)):
            raise MerchantError("invalid", where + "end time must look like 20:30.")
        end = datetime.combine(d, datetime.strptime(str(en), "%H:%M").time(), tzinfo=LUSAKA)
        if end == start:
            raise MerchantError("invalid", where + "end time must differ from the start time.")
        if end < start:               # e.g. 21:00 to 02:00 runs past midnight
            end += timedelta(days=1)
    return to_utc_iso(start), (to_utc_iso(end) if end else None), cap


def add_listing(conn, user_id, merchant_id, f, today):
    m = conn.execute("SELECT * FROM merchants WHERE id=?", (merchant_id,)).fetchone()
    if m is None:
        raise MerchantError("not_found", "Merchant not found.")
    if m["owner_user_id"] != user_id:
        raise MerchantError("forbidden", "Only the business owner can add listings.")
    title = _clean(f.get("title"), 120)
    if not title:
        raise MerchantError("invalid", "Title is required.")
    if f.get("listing_type") not in TYPES:
        raise MerchantError("invalid", "Type must be event, activity, dining or service.")
    price = f.get("price_ngwee")
    if isinstance(price, bool) or not isinstance(price, int) or not 1 <= price <= MAX_PRICE:
        raise MerchantError("invalid", "Price is required: more than zero and at most K50,000.")
    slots = f.get("slots") or []
    if not 1 <= len(slots) <= MAX_SLOTS:
        raise MerchantError("invalid", f"Add between 1 and {MAX_SLOTS} time slots.")
    parsed = [_parse_slot(s, today, i + 1) for i, s in enumerate(slots)]
    tags = [t.strip().lower()[:30] for t in (f.get("tags") or []) if isinstance(t, str) and t.strip()][:10]
    area = _clean(f.get("area"), 80) or m["area"]
    try:
        lid = conn.execute(
            "INSERT INTO listings(merchant_id, type, name, description, tags, area, price, serves_alcohol, "
            "source, status, trust_score, contact_whatsapp, is_sample) "
            "VALUES (?,?,?,?,?,?,?,?,'merchant','claimed',?,?,0)",
            (merchant_id, f["listing_type"], title, _clean(f.get("description"), 500),
             json.dumps(tags or [f["listing_type"]]), area, price, int(bool(f.get("serves_alcohol"))),
             CLAIMED_TRUST, m["whatsapp"])).lastrowid
        for st, en, cap in parsed:
            conn.execute("INSERT INTO slots(listing_id, starts_at, ends_at, capacity, booked) VALUES (?,?,?,?,0)",
                         (lid, st, en, cap))
        conn.execute("INSERT INTO audit_log(user_id, event, why, rule) VALUES (?,?,?,?)",
                     (user_id, "listing_claimed",
                      f"'{title}' was submitted by its owner. It stays hidden from the agent until Konsyer verifies the business.",
                      "merchant_listing_claimed"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"listing_id": lid, "status": "claimed", "slots": len(parsed),
            "message": "Submitted. It will appear to customers after Konsyer verifies your business."}


def get_merchant(conn, user_id, merchant_id):
    m = conn.execute("SELECT * FROM merchants WHERE id=?", (merchant_id,)).fetchone()
    if m is None or m["owner_user_id"] != user_id:
        raise MerchantError("not_found", "Merchant not found.")
    rows = conn.execute("SELECT id, name, type, price, status FROM listings WHERE merchant_id=? ORDER BY id",
                        (merchant_id,)).fetchall()
    return {"merchant_id": m["id"], "name": m["name"], "area": m["area"], "whatsapp": m["whatsapp"],
            "social_url": m["social_url"], "verified": bool(m["verified"]), "listings": [dict(r) for r in rows]}


def list_for_owner(conn, user_id):
    """The signed-in user's own businesses (not sample data), so the screen survives a restart."""
    ids = conn.execute("SELECT id FROM merchants WHERE owner_user_id=? AND is_sample=0 ORDER BY id", (user_id,))
    return [get_merchant(conn, user_id, r["id"]) for r in ids.fetchall()]


def admin_list(conn):
    """Every real merchant, those needing review first. Caller (the endpoint) must have checked the admin key."""
    out = []
    for m in conn.execute("SELECT * FROM merchants WHERE is_sample=0 ORDER BY id DESC").fetchall():
        counts = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM listings WHERE merchant_id=? GROUP BY status", (m["id"],))}
        out.append({"merchant_id": m["id"], "name": m["name"], "area": m["area"], "whatsapp": m["whatsapp"],
                    "social_url": m["social_url"], "verified": bool(m["verified"]),
                    "claimed_listings": counts.get("claimed", 0), "verified_listings": counts.get("verified", 0),
                    "needs_review": (not m["verified"]) or counts.get("claimed", 0) > 0})
    return sorted(out, key=lambda x: not x["needs_review"])


def verify(conn, merchant_id):
    """Admin only (the endpoint checks the key). Safe to call again: it verifies any new claimed listings."""
    m = conn.execute("SELECT * FROM merchants WHERE id=?", (merchant_id,)).fetchone()
    if m is None:
        raise MerchantError("not_found", "Merchant not found.")
    try:
        conn.execute("UPDATE merchants SET verified=1 WHERE id=?", (merchant_id,))
        n = conn.execute("UPDATE listings SET status='verified', trust_score=? "
                         "WHERE merchant_id=? AND status='claimed'", (VERIFIED_TRUST, merchant_id)).rowcount
        conn.execute("INSERT INTO audit_log(user_id, event, why, rule) VALUES (?,?,?,?)",
                     (m["owner_user_id"], "merchant_verified",
                      f"Konsyer verified '{m['name']}'. {n} listing(s) are now visible and ranked first.",
                      "admin_verified"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"merchant_id": merchant_id, "verified": True, "listings_verified": n}