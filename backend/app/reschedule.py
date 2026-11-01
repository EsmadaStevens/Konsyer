"""A verified seller can't make an appointment, so they offer a new time instead of just cancelling.

The customer chooses: accept the new time (same money, same booking, moved) or take a full refund. Nothing is
decided by a model. The seller's offer never pays the seller: an unanswered offer lapses into a full refund when the
original time arrives (see escrow.sweep).
"""
from datetime import timedelta

from .escrow import (AUTO_RELEASE_MINUTES, EscrowError, _audit, _get, _iso, _merchant_voucher, _must_be_held,
                     _refund, _transaction, now)
from .timeutil import fmt_local, parse_utc


def _open_dispute(conn, voucher_id):
    return conn.execute("SELECT 1 FROM disputes WHERE voucher_id=? AND status='open'", (voucher_id,)).fetchone()


def _booking(conn, v):
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (v["booking_id"],)).fetchone() if v["booking_id"] else None
    if b is None or b["status"] == "cancelled":
        raise EscrowError("conflict", "This booking has no time to move.")
    return b


def _usable_slot(conn, v, b, slot_id, stamp):
    """The new time must belong to the same listing, be in the future and have room for the customer's places."""
    s = conn.execute("SELECT * FROM slots WHERE id=? AND listing_id=?", (slot_id, v["listing_id"])).fetchone()
    if s is None or s["id"] == b["slot_id"]:
        raise EscrowError("invalid", "Pick another time from this listing.")
    if s["starts_at"] <= stamp:
        raise EscrowError("conflict", "That time has already started.")
    if s["capacity"] - s["booked"] < b["qty"]:
        raise EscrowError("conflict", "That time has no room left.")
    return s


def options(conn, owner_user_id, voucher_id, at=None):
    at = at or now(conn)
    v = _merchant_voucher(conn, owner_user_id, voucher_id)
    b = _booking(conn, v)
    rows = conn.execute("SELECT * FROM slots WHERE listing_id=? AND starts_at>? AND id IS NOT ? "
                        "AND capacity-booked>=? ORDER BY starts_at", (v["listing_id"], _iso(at), b["slot_id"], b["qty"]))
    return {"slots": [{"slot_id": s["id"], "starts": fmt_local(s["starts_at"]),
                       "ends": fmt_local(s["ends_at"]) if s["ends_at"] else None} for s in rows.fetchall()]}


def offer(conn, owner_user_id, voucher_id, slot_id, at=None):
    """The seller proposes a new time. Money, booking and slot counts stay exactly as they are until the customer answers."""
    at = at or now(conn)
    with _transaction(conn):
        v = _merchant_voucher(conn, owner_user_id, voucher_id)
        if _open_dispute(conn, voucher_id):
            raise EscrowError("conflict", "The customer has raised a problem with this booking. Refund or contest it.")
        b = _booking(conn, v)
        s = _usable_slot(conn, v, b, int(slot_id), _iso(at))
        conn.execute("UPDATE vouchers SET offer_slot_id=?, offered_at=? WHERE id=?", (s["id"], _iso(at), voucher_id))
        _audit(conn, v["user_id"], "reschedule_offered",
               f"'{v['label']}': the seller offered a new time, {fmt_local(s['starts_at'])}. Your money stays held "
               "until you answer.", "reschedule_offer")
    return {"ok": True}


def accept(conn, user_id, voucher_id, at=None):
    """The customer takes the new time. Same money, same booking, moved; the cancellation and release times follow it."""
    at = at or now(conn)
    with _transaction(conn):
        v = _get(conn, user_id, voucher_id)
        _must_be_held(v)
        if not v["offer_slot_id"]:
            raise EscrowError("conflict", "There is no new time to accept.")
        b = _booking(conn, v)
        try:
            s = _usable_slot(conn, v, b, v["offer_slot_id"], _iso(at))
        except EscrowError:
            raise EscrowError("conflict", "That time is no longer available. You can take a full refund.")
        conn.execute("UPDATE slots SET booked=MAX(booked-?,0) WHERE id=?", (b["qty"], b["slot_id"]))
        conn.execute("UPDATE slots SET booked=booked+? WHERE id=?", (b["qty"], s["id"]))
        conn.execute("UPDATE bookings SET slot_id=? WHERE id=?", (s["id"], b["id"]))
        if b["plan_item_id"]:
            conn.execute("UPDATE plan_items SET slot_id=?, starts_at=?, ends_at=? WHERE id=?",
                         (s["id"], s["starts_at"], s["ends_at"], b["plan_item_id"]))
        listing = conn.execute("SELECT cancel_free_hours FROM listings WHERE id=?", (v["listing_id"],)).fetchone()
        ends = s["ends_at"] or s["starts_at"]
        conn.execute(
            "UPDATE vouchers SET starts_at=?, ends_at=?, free_cancel_until=?, auto_release_at=?, offer_slot_id=NULL, "
            "offered_at=NULL WHERE id=?",
            (s["starts_at"], ends, _iso(parse_utc(s["starts_at"]) - timedelta(hours=int(listing["cancel_free_hours"]))),
             _iso(parse_utc(ends) + timedelta(minutes=AUTO_RELEASE_MINUTES)), voucher_id))
        _audit(conn, user_id, "reschedule_accepted",
               f"'{v['label']}' moved to {fmt_local(s['starts_at'])}. Your money is still held.", "reschedule_accept")
    return {"ok": True}


def decline(conn, user_id, voucher_id, at=None):
    """The customer does not want the new time: everything goes back, no cancellation fee, whatever the policy says."""
    at = at or now(conn)
    with _transaction(conn):
        v = _get(conn, user_id, voucher_id)
        _must_be_held(v)
        if not v["offer_slot_id"]:
            raise EscrowError("conflict", "There is no new time to turn down.")
        conn.execute("UPDATE vouchers SET offer_slot_id=NULL, offered_at=NULL WHERE id=?", (voucher_id,))
        v = conn.execute("SELECT * FROM vouchers WHERE id=?", (voucher_id,)).fetchone()
        _refund(conn, v, "declined_new_time", at)
    return {"ok": True}
