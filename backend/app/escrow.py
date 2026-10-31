"""Escrow ledger (SANDBOX money, simulated payouts). Plain code, no LLM.

A voucher is money held by Konsyer between a user and a seller:
  held -> released   (seller is paid)
  held -> refunded   (user gets everything back)
  held -> forfeited  (late cancel: the seller keeps part, the user gets the rest back)

Rules:
 * Only a human tap (an endpoint called by the app) creates, releases or refunds a voucher. The agent has no tool for it.
 * A VERIFIED seller (booking through the app): the deposit policy is fixed at booking time. If nobody objects, the
   money is released 24 hours after the service ends.
 * An UNVERIFIED seller (outside lead or flyer listing): the seller never agreed to anything, so the user can always
   cancel for a full refund, and money NEVER reaches them unless the user taps Release and confirms the payout number
   (typed into a dedicated field, validated here, shown back, and checked against the number on file).
   If the user does nothing, the money returns to their wallet after 7 days.
 * Nothing here moves real money. Payouts go through a labeled mock provider.
"""
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import discovery, policy, wallet
from .contact import one_line, parse_zm_phone, phone_kind
from .money import fmt_k
from .providers.payout import get_payout
from .timeutil import fmt_local, lusaka_today, parse_utc, to_utc_iso

AUTO_RELEASE_HOURS = 24
UNVERIFIED_EXPIRY_DAYS = 7
MIN_HOLD = 100  # K1
REPORT_REASONS = {"seller_cancelled", "not_delivered", "looks_like_scam", "closed_or_unreachable"}
# Network guess from the first two digits after the country code. Written from general knowledge and NOT verified:
# the UI says "looks like", never "is".
_NETWORKS = {"96": "MTN", "76": "MTN", "97": "Airtel", "77": "Airtel", "95": "Zamtel", "75": "Zamtel"}

STATUS = {"not_found": 404, "forbidden": 403, "invalid": 400, "conflict": 409, "confirm": 422}


class EscrowError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


# ---------- demo clock (so a video can fast-forward 24 hours) ----------
def now(conn) -> datetime:
    r = conn.execute("SELECT value FROM settings WHERE key='clock_offset_hours'").fetchone()
    return datetime.now(timezone.utc) + timedelta(hours=int(r["value"]) if r else 0)


def advance(conn, hours) -> dict:
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 24 * 30:
        raise EscrowError("invalid", "Hours must be a whole number from 1 to 720.")
    r = conn.execute("SELECT value FROM settings WHERE key='clock_offset_hours'").fetchone()
    total = (int(r["value"]) if r else 0) + hours
    conn.execute("INSERT INTO settings(key, value) VALUES ('clock_offset_hours', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(total),))
    conn.commit()
    return {"offset_hours": total, "demo_now": fmt_local(to_utc_iso(now(conn)))}


# ---------- helpers ----------
@contextmanager
def _transaction(conn):
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _audit(conn, user_id, event, why, rule):
    conn.execute("INSERT INTO audit_log(user_id, event, why, rule) VALUES (?,?,?,?)", (user_id, event, why, rule))


def _limits(conn, user_id):
    p = conn.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    if p is None:
        raise EscrowError("not_found", "User not found.")
    return policy.Limits(p["autonomy"], p["per_purchase_cap"], p["daily_cap"], p["auto_buy_under"],
                         frozenset(json.loads(p["pre_approved_merchants"] or "[]")))


def _iso(dt) -> str:
    return to_utc_iso(dt)


def _get(conn, user_id, voucher_id):
    v = conn.execute("SELECT * FROM vouchers WHERE id=? AND user_id=?", (voucher_id, user_id)).fetchone()
    if v is None:
        raise EscrowError("not_found", "That voucher was not found.")
    return v


def _must_be_held(v):
    if v["state"] != "held":
        raise EscrowError("conflict", f"This is already {v['state']}. Nothing more can be done.")


def check_number(raw, on_file=None) -> dict:
    """Validate a payout number in code. Returns the normalised number, a 'looks like' network note and a warning
    when it differs from the number on file (or none is on file). Never trusts text from a listing or the model."""
    digits = parse_zm_phone(raw)
    if not digits or phone_kind(digits) != "mobile":
        raise EscrowError("invalid", "Enter a Zambian mobile number, for example 0977 123 456.")
    network = _NETWORKS.get(digits[3:5])
    on_file_digits = parse_zm_phone(on_file) if on_file else None
    warning = None
    if on_file_digits is None:
        warning = "No number for this seller is on file, so check this one with the seller yourself."
    elif on_file_digits != digits:
        warning = (f"This is different from the number on the seller's page or listing (+{on_file_digits}). "
                   "Only continue if the seller told you to use this one.")
    return {"number": digits, "display": f"+{digits}",
            "network_note": (f"Looks like a {network}-range number (not verified)" if network
                             else "Network not recognised"),
            "warning": warning}


def _release(conn, v, phone, actor, resolution, at):
    receipt = get_payout().send(v["amount"], phone, f"voucher-{v['id']}-release", v["label"])
    conn.execute("UPDATE vouchers SET state='released', paid_amount=amount, resolution=?, payout_ref=?, "
                 "resolved_at=? WHERE id=?", (resolution, receipt["payout_id"], _iso(at), v["id"]))
    wallet.note(conn, v["user_id"], "release", f"voucher-{v['id']}-release", v["label"])
    _audit(conn, v["user_id"], "voucher_released",
           f"{fmt_k(v['amount'])} for '{v['label']}' was released to the seller ({actor}). Simulated payout "
           f"{receipt['payout_id']}.", f"voucher_{resolution}")


def _cancel_booking(conn, v):
    if not v["booking_id"]:
        return
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (v["booking_id"],)).fetchone()
    if b and b["status"] != "cancelled":
        conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (b["id"],))
        if b["slot_id"]:
            conn.execute("UPDATE slots SET booked = MAX(booked - ?, 0) WHERE id=?", (b["qty"], b["slot_id"]))
        if b["plan_item_id"]:
            conn.execute("UPDATE plan_items SET status='cancelled' WHERE id=?", (b["plan_item_id"],))


def _refund(conn, v, resolution, at, paid=0):
    back = v["amount"] - paid
    if back > 0:
        wallet.refund(conn, v["user_id"], back, f"voucher-{v['id']}-refund", v["label"])
    receipt = None
    if paid > 0:
        receipt = get_payout().send(paid, v["payee_phone"], f"voucher-{v['id']}-forfeit", v["label"])
    state = "forfeited" if paid > 0 else "refunded"
    conn.execute("UPDATE vouchers SET state=?, paid_amount=?, refunded_amount=?, resolution=?, payout_ref=?, "
                 "resolved_at=? WHERE id=?",
                 (state, paid, back, resolution, receipt["payout_id"] if receipt else None, _iso(at), v["id"]))
    _cancel_booking(conn, v)
    extra = f" The seller keeps {fmt_k(paid)} under the cancellation policy." if paid else ""
    _audit(conn, v["user_id"], f"voucher_{state}",
           f"{fmt_k(back)} for '{v['label']}' went back to your wallet.{extra}", f"voucher_{resolution}")


# ---------- creating vouchers ----------
def hold_booking(conn, user_id, booking_id, listing, slot, amount, label, at=None) -> int:
    """Called by the executor INSIDE its transaction, right after a booking is paid from the wallet. The money
    has already left the spendable balance; this records that it is HELD and under which policy."""
    at = at or now(conn)
    verified = listing["status"] == "verified"
    starts = slot["starts_at"]
    ends = slot["ends_at"] or starts
    if verified:
        free_until = _iso(parse_utc(starts) - timedelta(hours=int(listing["cancel_free_hours"])))
        forfeit = int(listing["late_forfeit_pct"])
        auto_at, expires = _iso(parse_utc(ends) + timedelta(hours=AUTO_RELEASE_HOURS)), None
    else:  # unverified seller: always a full refund on cancel, never auto-paid
        free_until, forfeit, auto_at = None, 0, None
        expires = _iso(parse_utc(ends) + timedelta(days=UNVERIFIED_EXPIRY_DAYS))
    return conn.execute(
        "INSERT INTO vouchers(user_id, kind, label, amount, booking_id, listing_id, merchant_id, payee_name, "
        "payee_phone, payee_verified, starts_at, ends_at, free_cancel_until, forfeit_pct, auto_release_at, "
        "expires_at, idem_key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, "booking", label, amount, booking_id, listing["id"], listing["merchant_id"], listing["name"],
         parse_zm_phone(listing["contact_whatsapp"]), int(verified), starts, ends, free_until, forfeit, auto_at,
         expires, f"booking-{booking_id}", _iso(at))).lastrowid


def create_lead_hold(conn, user_id, lead_id, amount, what=None, idem_key=None, today=None, at=None) -> dict:
    """The user chose to hold `amount` for an outside (unverified) lead. The amount is the user's own confirm step,
    never text from the lead. Policy caps still apply; the unverified-payee rule is satisfied by this explicit tap."""
    today, at = today or lusaka_today(), at or now(conn)
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < MIN_HOLD:
        raise EscrowError("invalid", "Enter the amount the seller quoted, at least K1.")
    try:
        lead = discovery.get_lead(conn, int(lead_id))
    except (TypeError, ValueError):
        lead = None
    if lead is None:
        raise EscrowError("not_found", "That lead is no longer available (leads last 24 hours). Search again.")
    key = f"lead-{user_id}-{idem_key or uuid.uuid4().hex}"
    with _transaction(conn):
        dup = conn.execute("SELECT * FROM vouchers WHERE idem_key=? AND user_id=?", (key, user_id)).fetchone()
        if dup:
            return {"ok": True, "outcome": "already_held", "voucher": _view(dup, at)}
        d = policy.decide(_limits(conn, user_id),
                          policy.Context(wallet.balance(conn, user_id), wallet.spent_today(conn, user_id, today)),
                          policy.Action(amount, None, False, 0.3))
        if d.verdict == policy.Verdict.DENY:
            _audit(conn, user_id, "hold_blocked", d.reason, d.rule)
            return {"ok": False, "outcome": "blocked_by_policy", "rule": d.rule, "message": d.reason}
        label = f"{one_line(what, 80) or 'Payment'} to {one_line(lead['name'], 80)}"
        try:
            wallet.debit(conn, user_id, amount, f"{key}-debit", label, kind="hold")
        except wallet.WalletError as e:
            return {"ok": False, "outcome": "blocked_by_policy", "rule": "insufficient_funds", "message": str(e)}
        vid = conn.execute(
            "INSERT INTO vouchers(user_id, kind, label, amount, lead_id, payee_name, payee_phone, payee_verified, "
            "expires_at, idem_key, created_at) VALUES (?,?,?,?,?,?,?,0,?,?,?)",
            (user_id, "lead", label, amount, lead["id"], lead["name"], parse_zm_phone(lead["phone"]),
             _iso(at + timedelta(days=UNVERIFIED_EXPIRY_DAYS)), key, _iso(at))).lastrowid
        _audit(conn, user_id, "voucher_held",
               f"{fmt_k(amount)} is held for '{label}'. This seller is not verified, so nothing is paid out until "
               "you tap Release.", "lead_hold_by_user")
        v = conn.execute("SELECT * FROM vouchers WHERE id=?", (vid,)).fetchone()
    return {"ok": True, "outcome": "held", "voucher": _view(v, at)}


# ---------- user actions ----------
def payout_check(conn, user_id, voucher_id, payout_number) -> dict:
    v = _get(conn, user_id, voucher_id)
    _must_be_held(v)
    if v["payee_verified"]:
        raise EscrowError("invalid", "This seller is verified, so Konsyer pays their registered account.")
    return check_number(payout_number, v["payee_phone"])


def release(conn, user_id, voucher_id, payout_number=None, confirmed_number=None, accept_warning=False, at=None):
    """The user says 'I received the service / item'. Verified seller: pays their registered account. Unverified
    seller: needs a validated payout number that the user confirmed on screen."""
    at = at or now(conn)
    with _transaction(conn):
        v = _get(conn, user_id, voucher_id)
        _must_be_held(v)
        if v["payee_verified"]:
            phone = v["payee_phone"]
        else:
            if not payout_number:
                raise EscrowError("invalid", "Enter the seller's mobile-money number first.")
            chk = check_number(payout_number, v["payee_phone"])
            if parse_zm_phone(confirmed_number) != chk["number"]:
                raise EscrowError("confirm", "Please confirm the number shown on screen before releasing.")
            if chk["warning"] and not accept_warning:
                raise EscrowError("confirm", chk["warning"])
            phone = chk["number"]
        _release(conn, v, phone, "you confirmed", "released_by_user", at)
        v = conn.execute("SELECT * FROM vouchers WHERE id=?", (voucher_id,)).fetchone()
    return {"ok": True, "voucher": _view(v, at)}


def cancel(conn, user_id, voucher_id, at=None):
    at = at or now(conn)
    with _transaction(conn):
        v = _get(conn, user_id, voucher_id)
        _must_be_held(v)
        paid, resolution = 0, "cancelled_by_user"
        if v["payee_verified"] and v["free_cancel_until"] and _iso(at) > v["free_cancel_until"]:
            paid, resolution = v["amount"] * v["forfeit_pct"] // 100, "cancelled_late"
        _refund(conn, v, resolution, at, paid)
        v = conn.execute("SELECT * FROM vouchers WHERE id=?", (voucher_id,)).fetchone()
    return {"ok": True, "voucher": _view(v, at)}


def report(conn, user_id, voucher_id, reason, at=None):
    """Something went wrong (seller cancelled, never delivered, looked like a scam). Full refund while still held.
    A verified seller's no-show can only be reported once the booking time has started."""
    at = at or now(conn)
    if reason not in REPORT_REASONS:
        raise EscrowError("invalid", "Pick one of: " + ", ".join(sorted(REPORT_REASONS)) + ".")
    with _transaction(conn):
        v = _get(conn, user_id, voucher_id)
        _must_be_held(v)
        if v["payee_verified"] and v["starts_at"] and _iso(at) < v["starts_at"]:
            raise EscrowError("conflict", "The booking time has not started yet. You can cancel instead.")
        _refund(conn, v, f"reported_{reason}", at)
        v = conn.execute("SELECT * FROM vouchers WHERE id=?", (voucher_id,)).fetchone()
    return {"ok": True, "voucher": _view(v, at)}


# ---------- seller actions (verified merchants only) ----------
def _merchant_voucher(conn, owner_user_id, voucher_id):
    v = conn.execute("SELECT * FROM vouchers WHERE id=?", (voucher_id,)).fetchone()
    m = conn.execute("SELECT * FROM merchants WHERE id=?", (v["merchant_id"],)).fetchone() if v and v["merchant_id"] else None
    if not v or not m or m["owner_user_id"] != owner_user_id or not v["payee_verified"]:
        raise EscrowError("not_found", "That booking was not found for your business.")
    _must_be_held(v)
    return v


def merchant_confirm(conn, owner_user_id, voucher_id, at=None):
    """The business confirms the customer came. Only once the booking time has started."""
    at = at or now(conn)
    with _transaction(conn):
        v = _merchant_voucher(conn, owner_user_id, voucher_id)
        if v["starts_at"] and _iso(at) < v["starts_at"]:
            raise EscrowError("conflict", "The booking time has not started yet.")
        _release(conn, v, v["payee_phone"], "the business confirmed", "released_by_merchant", at)
        v = conn.execute("SELECT * FROM vouchers WHERE id=?", (voucher_id,)).fetchone()
    return {"ok": True, "voucher": _view(v, at)}


def merchant_cancel(conn, owner_user_id, voucher_id, at=None):
    """The business cancels: the customer always gets everything back."""
    at = at or now(conn)
    with _transaction(conn):
        v = _merchant_voucher(conn, owner_user_id, voucher_id)
        _refund(conn, v, "cancelled_by_seller", at)
        v = conn.execute("SELECT * FROM vouchers WHERE id=?", (voucher_id,)).fetchone()
    return {"ok": True, "voucher": _view(v, at)}


# ---------- time-based settling (no background job: runs whenever vouchers are listed) ----------
def sweep(conn, user_id=None, at=None) -> int:
    at = at or now(conn)
    stamp, n = _iso(at), 0
    with _transaction(conn):
        rows = conn.execute("SELECT * FROM vouchers WHERE state='held' AND (? IS NULL OR user_id=?)",
                            (user_id, user_id)).fetchall()
        for v in rows:
            if v["auto_release_at"] and v["auto_release_at"] <= stamp:
                _release(conn, v, v["payee_phone"], "nobody objected for 24 hours", "auto_released", at)
                n += 1
            elif v["expires_at"] and v["expires_at"] <= stamp:
                _refund(conn, v, "expired", at)
                n += 1
    return n


# ---------- reading ----------
def held_total(conn, user_id) -> int:
    return conn.execute("SELECT COALESCE(SUM(amount),0) FROM vouchers WHERE user_id=? AND state='held'",
                        (user_id,)).fetchone()[0]


def _policy_text(v) -> str:
    if v["state"] != "held":
        return ""
    if not v["payee_verified"]:
        return ("This seller is not verified. Konsyer only pays them when you tap Release. You can cancel for a "
                f"full refund any time. If you do nothing, the money returns to your wallet on "
                f"{fmt_local(v['expires_at'])}.")
    cancel = (f"Free cancellation until {fmt_local(v['free_cancel_until'])}; after that the seller keeps "
              f"{v['forfeit_pct']}%." if v["forfeit_pct"] else "You can cancel for a full refund any time.")
    return (f"Held by Konsyer until you check in. {cancel} If nobody objects, it is released to the seller on "
            f"{fmt_local(v['auto_release_at'])}.")


def _view(v, at) -> dict:
    held = v["state"] == "held"
    stamp = _iso(at)
    return {"id": v["id"], "kind": v["kind"], "label": v["label"], "state": v["state"],
            "amount": v["amount"], "amount_display": fmt_k(v["amount"]),
            "paid_amount": v["paid_amount"], "refunded_amount": v["refunded_amount"],
            "payee_name": v["payee_name"], "payee_verified": bool(v["payee_verified"]),
            "payee_phone": f"+{v['payee_phone']}" if v["payee_phone"] else None,
            "needs_payout_number": held and not v["payee_verified"],
            "policy_text": _policy_text(v), "resolution": v["resolution"], "payout_ref": v["payout_ref"],
            "starts": fmt_local(v["starts_at"]) if v["starts_at"] else None,
            "can_release": held, "can_cancel": held,
            "can_report": held and not (v["payee_verified"] and v["starts_at"] and stamp < v["starts_at"]),
            "simulated": True}


def list_for_user(conn, user_id, at=None) -> list:
    at = at or now(conn)
    sweep(conn, user_id, at)
    rows = conn.execute("SELECT * FROM vouchers WHERE user_id=? ORDER BY (state='held') DESC, id DESC",
                        (user_id,)).fetchall()
    return [_view(v, at) for v in rows]


def list_for_merchant(conn, owner_user_id, merchant_id, at=None) -> list:
    at = at or now(conn)
    m = conn.execute("SELECT owner_user_id FROM merchants WHERE id=?", (merchant_id,)).fetchone()
    if m is None or m["owner_user_id"] != owner_user_id:
        raise EscrowError("not_found", "Business not found.")
    sweep(conn, None, at)
    rows = conn.execute("SELECT * FROM vouchers WHERE merchant_id=? AND payee_verified=1 "
                        "ORDER BY (state='held') DESC, id DESC", (merchant_id,)).fetchall()
    out = []
    for v in rows:
        d = _view(v, at)
        d.update(can_release=False, can_cancel=False, can_report=False,
                 can_confirm=v["state"] == "held" and not (v["starts_at"] and _iso(at) < v["starts_at"]),
                 can_seller_cancel=v["state"] == "held")
        out.append(d)
    return out