"""Executor: the ONLY place an approved action becomes real (here: sandbox wallet + booking rows).
Approval comes only from the approve endpoint calling approve(), never from text a model reads.
Pending actions do not reserve wallet money, so approve() re-runs the policy at that moment."""
import json
import secrets
from datetime import datetime, timezone


from . import escrow, policy, wallet
from .money import fmt_k
from .timeutil import lusaka_today, to_local

_LIVE = "('pending','approved','executed')"


class ExecError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(conn, user_id, action_id, event, why, rule=None):
    conn.execute("INSERT INTO audit_log(user_id, pending_action_id, event, why, rule) VALUES (?,?,?,?,?)",
                 (user_id, action_id, event, why, rule))


def _limits(conn, user_id):
    p = conn.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    return policy.Limits(p["autonomy"], p["per_purchase_cap"], p["daily_cap"], p["auto_buy_under"],
                         frozenset(json.loads(p["pre_approved_merchants"] or "[]")))


def _plan_remaining(conn, plan_id, exclude):
    if plan_id is None:
        return None
    p = conn.execute("SELECT budget FROM plans WHERE id=?", (plan_id,)).fetchone()
    if p is None or p["budget"] is None:
        return None
    used = conn.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM pending_actions WHERE plan_id=? AND status IN {_LIVE} AND id IS NOT ?",
        (plan_id, exclude)).fetchone()[0]
    return p["budget"] - used


def _set_status(conn, action_id, status, **extra):
    conn.execute("UPDATE pending_actions SET status=?, decided_at=? WHERE id=?", (status, _now(), action_id))
    if extra:
        conn.execute("UPDATE pending_actions SET policy_decision=?, policy_rule=?, policy_reason=? WHERE id=?",
                     (extra["decision"], extra["rule"], extra["reason"], action_id))


def _fail(conn, user_id, action_id, message):
    conn.rollback()  # undo any partial writes, then record the failure
    _set_status(conn, action_id, "failed")
    _audit(conn, user_id, action_id, "execution_failed", message, "execution_check")
    return {"ok": False, "outcome": "failed", "action_id": action_id, "message": message}


def approve(conn, user_id, action_id, today=None):
    """User approved this action. Re-check policy, then execute inside one transaction."""
    today = today or lusaka_today()
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")  # lock: nothing else can change the wallet while we check and write
    try:
        result = _approve_locked(conn, user_id, action_id, today)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def _approve_locked(conn, user_id, action_id, today):
    row = conn.execute("SELECT * FROM pending_actions WHERE id=? AND user_id=?", (action_id, user_id)).fetchone()
    if not row:
        raise ExecError("not_found", "That action was not found.")
    if row["status"] == "executed":
        return {"ok": True, "outcome": "already_executed", "action_id": action_id,
                "message": "This was already done. Nothing was charged again.",
                "balance_ngwee": wallet.balance(conn, user_id)}
    if row["status"] not in ("pending", "approved"):
        raise ExecError("not_approvable", f"This action is {row['status']} and cannot be approved.")

    payload, kind = json.loads(row["payload"]), row["kind"]
    listing = slot = None
    if kind == "book_listing":
        listing = conn.execute("SELECT * FROM listings WHERE id=?", (payload["listing_id"],)).fetchone()
        slot = conn.execute("SELECT * FROM slots WHERE id=?", (payload["slot_id"],)).fetchone()
        if not listing or not slot or listing["price"] is None:
            return _fail(conn, user_id, action_id, "This listing is no longer available.")
        amount = listing["price"] * payload["qty"]  # recomputed from the database
        if amount != row["amount"]:
            return _fail(conn, user_id, action_id, "The price changed since this was proposed. Please plan again.")
        merchant_id, verified, trust = listing["merchant_id"], listing["status"] == "verified", listing["trust_score"]
    elif kind == "ride_home":
        amount, merchant_id, verified, trust = row["amount"], None, True, 1.0
    else:
        raise ExecError("not_approvable", f"Unsupported action kind: {kind}")

    ctx = policy.Context(wallet.balance(conn, user_id), wallet.spent_today(conn, user_id, today),
                         _plan_remaining(conn, row["plan_id"], action_id))
    d = policy.decide(_limits(conn, user_id), ctx, policy.Action(amount, merchant_id, verified, trust))
    if d.verdict == policy.Verdict.DENY:
        _set_status(conn, action_id, "denied", decision="DENY", rule=d.rule, reason=d.reason)
        _audit(conn, user_id, action_id, "approval_blocked", d.reason, d.rule)
        return {"ok": False, "outcome": "blocked_by_policy", "action_id": action_id,
                "rule": d.rule, "message": d.reason}

    # CONFIRM or ALLOW: the user's approval satisfies CONFIRM rules.
    _audit(conn, user_id, action_id, "approved_by_user",
           f"You approved it. Checked again just now: {d.reason}", d.rule)

    plan_item_id = None
    if kind == "book_listing":
        if slot["capacity"] - slot["booked"] < payload["qty"]:
            return _fail(conn, user_id, action_id, "Not enough spots are left any more. Nothing was charged.")
        conn.execute("UPDATE slots SET booked = booked + ? WHERE id=?", (payload["qty"], slot["id"]))
    try:
        wallet.debit(conn, user_id, amount, f"action-{action_id}-debit", payload.get("label", kind))
    except wallet.WalletError as e:
        return _fail(conn, user_id, action_id, f"Payment failed: {e}. Nothing was charged.")

    result = {"ok": True, "outcome": "executed", "action_id": action_id}
    if row["plan_id"] is not None:
        item_kind = "booking" if kind == "book_listing" else "ride_home"
        starts = slot["starts_at"] if slot else payload.get("pickup_at")
        plan_item_id = conn.execute(
            "INSERT INTO plan_items(plan_id, kind, listing_id, slot_id, starts_at, ends_at, amount, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (row["plan_id"], item_kind, listing["id"] if listing else None, slot["id"] if slot else None,
             starts, slot["ends_at"] if slot else None, amount,
             "booked" if kind == "book_listing" else "scheduled")).lastrowid
    if kind == "book_listing":
        qr = secrets.token_urlsafe(16)
        bid = conn.execute(
            "INSERT INTO bookings(plan_item_id, user_id, listing_id, slot_id, qty, amount, qr_token, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (plan_item_id, user_id, listing["id"], slot["id"], payload["qty"], amount, qr, "confirmed")).lastrowid
        result["booking"] = {"booking_id": bid, "qr_token": qr}
        vid = escrow.hold_booking(conn, user_id, bid, listing, slot, amount, payload.get("label", "Booking"))
        result["voucher_id"] = vid
        why = (f"Booked {payload['label']} for {fmt_k(amount)} from your sandbox wallet. "
               "The money is held by Konsyer until you check in.")
    else:
        why = f"Ride home scheduled for {fmt_k(amount)} (simulated provider, sandbox wallet)."
    _set_status(conn, action_id, "executed")
    _audit(conn, user_id, action_id, "executed", why, d.rule)
    result.update(message=why, balance_ngwee=wallet.balance(conn, user_id))
    return result


def _ride_still_needed(conn, plan_id):
    """True if a live booking in the plan serves alcohol or ends late (same rule as the auto-ride)."""
    for r in conn.execute(f"SELECT payload FROM pending_actions WHERE plan_id=? AND kind='book_listing' "
                          f"AND status IN {_LIVE}", (plan_id,)):
        p = json.loads(r["payload"])
        l = conn.execute("SELECT * FROM listings WHERE id=?", (p["listing_id"],)).fetchone()
        s = conn.execute("SELECT * FROM slots WHERE id=?", (p["slot_id"],)).fetchone()
        if not l or not s:
            continue
        end = s["ends_at"] or l["ends_at"]
        late = bool(end) and (to_local(end).hour >= 21 or to_local(end).date() > to_local(s["starts_at"]).date())
        if l["serves_alcohol"] or late:
            return True
    return False


def deny(conn, user_id, action_id):
    row = conn.execute("SELECT * FROM pending_actions WHERE id=? AND user_id=?", (action_id, user_id)).fetchone()
    if not row:
        raise ExecError("not_found", "That action was not found.")
    if row["status"] == "denied":
        return {"ok": True, "outcome": "denied", "action_id": action_id, "message": "Already declined."}
    if row["status"] not in ("pending", "approved"):
        raise ExecError("not_approvable", f"This action is {row['status']} and cannot be declined.")
    _set_status(conn, action_id, "denied")
    _audit(conn, user_id, action_id, "denied_by_user", "You declined this.", "user_declined")
    if row["kind"] == "book_listing" and row["plan_id"] is not None and not _ride_still_needed(conn, row["plan_id"]):
        ride = conn.execute("SELECT id FROM pending_actions WHERE plan_id=? AND kind='ride_home' "
                            "AND status IN ('pending','approved')", (row["plan_id"],)).fetchone()
        if ride:
            _set_status(conn, ride["id"], "denied")
            _audit(conn, user_id, ride["id"], "ride_cancelled",
                   "Ride home cancelled because nothing left in this plan needs one.", "ride_not_needed")
    conn.commit()
    return {"ok": True, "outcome": "denied", "action_id": action_id, "message": "Declined. Nothing was charged."}


def list_pending(conn, user_id):
    out = []
    for r in conn.execute("SELECT * FROM pending_actions WHERE user_id=? AND status IN ('pending','approved') "
                          "ORDER BY id", (user_id,)):
        p = json.loads(r["payload"])
        out.append({"action_id": r["id"], "plan_id": r["plan_id"], "label": p.get("label", r["kind"]),
                    "amount": r["amount"], "decision": r["policy_decision"], "reason": r["policy_reason"],
                    "status": r["status"]})
    return out