"""Flyer / screenshot ingest. The model READS (extraction.py); the user CONFIRMS; plain code validates and writes.
Only confirm() can create a listing, and only the confirm endpoint calls it: no agent tool can.
The uploaded image goes to a temp file for the model call and is deleted straight after. It is never stored."""
import json
import logging
import os
import re
import tempfile
from datetime import date, datetime, timedelta

from .extraction import extract_flyer
from .money import fmt_k

log = logging.getLogger("konsyer.flyers")

MAX_BYTES = 10 * 1024 * 1024
MAX_PRICE = 5_000_000          # K50,000 in ngwee
PLACEHOLDER_CAPACITY = 100     # a flyer does not print capacity; this is a placeholder, not a real number
KINDS = ("flyer", "screenshot")
TYPES = ("event", "activity", "dining", "service")
STATUS = {"bad_image": 415, "too_big": 413, "not_found": 404, "invalid": 422,
          "extract_failed": 502, "conflict": 409}
_UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
_CAT = timedelta(hours=2)      # Lusaka is UTC+2, no daylight saving


class FlyerError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def detect_image(data: bytes):
    """File type from the first bytes, not from the filename. Returns an extension or None."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def normalize_phone(text):
    """Digits only, Zambian local format 0XXXXXXXXX -> 260XXXXXXXXX (the form wa.me links need)."""
    if not text or not str(text).strip():
        return None
    digits = re.sub(r"\D", "", str(text))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        digits = "260" + digits[1:]
    if not 9 <= len(digits) <= 15:
        raise ValueError("phone")
    return digits


def create_draft(conn, user_id, kind, data, today):
    if kind not in KINDS:
        raise FlyerError("invalid", "kind must be 'flyer' or 'screenshot'.")
    if not data:
        raise FlyerError("bad_image", "The file is empty.")
    if len(data) > MAX_BYTES:
        raise FlyerError("too_big", f"That image is too large (the limit is {MAX_BYTES // (1024 * 1024)} MB).")
    ext = detect_image(data)
    if ext is None:
        raise FlyerError("bad_image", "Please upload a JPEG, PNG or WebP image.")
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        raise FlyerError("not_found", "User not found.")

    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        draft = extract_flyer(path, today)
    except Exception as e:
        log.exception("flyer extraction failed")
        raise FlyerError("extract_failed", "Could not read that image. Try a clearer photo.") from e
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    conf = draft.get("model_confidence")
    conf = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
    fid = conn.execute("INSERT INTO flyers(user_id, source, extracted, confidence, status) VALUES (?,?,?,?, 'draft')",
                       (user_id, kind, json.dumps(draft, ensure_ascii=False), conf)).lastrowid
    conn.commit()
    return {"flyer_id": fid, "kind": kind, "draft": draft}


def _description(kind, ltype, f, performers, tiers):
    parts = [f"Added from an uploaded {kind}. The uploader confirmed these details; Konsyer has not verified them."]
    if ltype == "event":
        parts.append("Lineup: " + ", ".join(performers) + "." if performers else "Lineup not listed.")
    if tiers:
        parts.append("Prices printed on the " + kind + ": " + "; ".join(
            f"{(t.get('label') or 'Ticket')} {fmt_k(t['amount'])}" for t in tiers
            if isinstance(t.get("amount"), int)) + ".")
    if f.get("availability_text"):
        parts.append("Availability: " + str(f["availability_text"]).strip()[:200] + ".")
    return " ".join(parts)


def confirm(conn, user_id, flyer_id, f, today):
    """The user reviewed the draft and sends the final fields. Re-validate everything; never trust the draft."""
    row = conn.execute("SELECT * FROM flyers WHERE id=? AND user_id=?", (flyer_id, user_id)).fetchone()
    if not row:
        raise FlyerError("not_found", "That flyer was not found.")
    tag = f"flyer:{flyer_id}"
    if row["status"] == "confirmed":
        ex = conn.execute("SELECT id FROM listings WHERE source_url=?", (tag,)).fetchone()
        return {"flyer_id": flyer_id, "listing_id": ex["id"] if ex else None, "already_confirmed": True,
                "message": "This was already added. Nothing was duplicated."}

    errors = []
    title = (f.get("title") or "").strip()[:120]
    if not title:
        errors.append("Title is required.")
    ltype = f.get("listing_type")
    if ltype not in TYPES:
        errors.append("Type must be event, activity, dining or service.")

    start_utc = None
    if ltype != "service":
        try:
            d = date.fromisoformat(str(f.get("date") or ""))
        except ValueError:
            d = None
            errors.append("Date must look like 2026-10-31.")
        t = str(f.get("start_time") or "")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            errors.append("Start time must look like 20:00.")
        elif d is not None:
            if d < today:
                errors.append("That date is in the past.")
            else:
                local = datetime.combine(d, datetime.strptime(t, "%H:%M").time())
                start_utc = (local - _CAT).strftime(_UTC_FMT)

    price = f.get("price_ngwee")
    if price is not None and (isinstance(price, bool) or not isinstance(price, int) or not 1 <= price <= MAX_PRICE):
        errors.append("Price must be more than zero and at most K50,000.")

    phone = None
    try:
        phone = normalize_phone(f.get("contact_phone"))
    except ValueError:
        errors.append("That phone number does not look right.")

    performers = [p.strip()[:80] for p in (f.get("performers") or []) if isinstance(p, str) and p.strip()][:20]
    if errors:
        raise FlyerError("invalid", " ".join(errors))

    venue = (f.get("venue") or "").strip()[:120] or None
    area = (f.get("area") or "").strip()[:80] or venue
    try:
        tiers = json.loads(row["extracted"] or "{}").get("prices") or []
    except ValueError:
        tiers = []
    desc = _description(row["source"], ltype, f, performers, tiers)

    try:
        cur = conn.execute("UPDATE flyers SET status='confirmed' WHERE id=? AND status='draft'", (flyer_id,))
        if cur.rowcount == 0:
            raise FlyerError("conflict", "This flyer was just confirmed somewhere else.")
        lid = conn.execute(
            "INSERT INTO listings(type, name, description, tags, area, price, serves_alcohol, starts_at, source, "
            "status, trust_score, contact_whatsapp, source_url, is_sample) "
            "VALUES (?,?,?,?,?,?,?,?,?,'unverified',0.3,?,?,0)",
            (ltype, title, desc, json.dumps([ltype]), area, price, int(bool(f.get("serves_alcohol"))),
             start_utc, row["source"], phone, tag)).lastrowid
        sid = None
        if start_utc:
            sid = conn.execute("INSERT INTO slots(listing_id, starts_at, ends_at, capacity, booked) "
                               "VALUES (?,?,NULL,?,0)", (lid, start_utc, PLACEHOLDER_CAPACITY)).lastrowid
        conn.execute("INSERT INTO audit_log(user_id, event, why, rule) VALUES (?,?,?,?)",
                     (user_id, "listing_created_from_flyer",
                      f"You confirmed '{title}' from your uploaded {row['source']}. It is unverified, "
                      "so paying for it will always need your approval.", "flyer_confirmed_by_user"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"flyer_id": flyer_id, "listing_id": lid, "slot_id": sid, "status": "unverified",
            "message": f"Added '{title}' as an unverified listing. Paying for it will always ask for your approval."}