"""Sandbox wallet ledger. Money is integer ngwee. Plain code, no LLM.
Functions do NOT commit: the caller commits, so a whole approval is one transaction.
Spending is stored as a negative 'purchase' row; top-ups are positive 'topup' rows."""
from datetime import datetime, time, timedelta

MIN_TOPUP = 100          # K1
MAX_TOPUP = 1_000_000    # K10,000 per top-up (sandbox limit)
_LUSAKA_OFFSET = timedelta(hours=2)  # CAT is UTC+2, no daylight saving
_FMT = "%Y-%m-%d %H:%M:%S"           # same format as SQLite CURRENT_TIMESTAMP (UTC)


class WalletError(Exception):
    pass


class InsufficientFunds(WalletError):
    pass


def balance(conn, user_id) -> int:
    r = conn.execute("SELECT balance FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    return r["balance"] if r else 0


def spent_today(conn, user_id, today) -> int:
    """Purchases made during the Lusaka calendar day `today` (a date)."""
    start = datetime.combine(today, time.min) - _LUSAKA_OFFSET  # Lusaka midnight, in UTC
    end = start + timedelta(days=1)
    return conn.execute(
        "SELECT COALESCE(-SUM(amount),0) FROM wallet_txns "
        "WHERE user_id=? AND kind='purchase' AND created_at>=? AND created_at<?",
        (user_id, start.strftime(_FMT), end.strftime(_FMT))).fetchone()[0]


def _existing(conn, key, user_id):
    r = conn.execute("SELECT * FROM wallet_txns WHERE idempotency_key=?", (key,)).fetchone()
    if r and r["user_id"] != user_id:
        raise WalletError("idempotency key belongs to another user")
    return r


def topup(conn, user_id, amount, key, ref="sandbox top-up") -> dict:
    amount = int(amount)
    if not MIN_TOPUP <= amount <= MAX_TOPUP:
        raise WalletError("Top-up must be between K1 and K10,000.")
    if not key:
        raise WalletError("idempotency key required")
    if _existing(conn, key, user_id):
        return {"duplicate": True, "balance": balance(conn, user_id)}
    conn.execute("INSERT OR IGNORE INTO wallets(user_id, balance) VALUES (?, 0)", (user_id,))
    conn.execute("UPDATE wallets SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.execute("INSERT INTO wallet_txns(user_id, amount, kind, idempotency_key, ref) VALUES (?,?,?,?,?)",
                 (user_id, amount, "topup", key, ref))
    return {"duplicate": False, "balance": balance(conn, user_id)}


def debit(conn, user_id, amount, key, ref="") -> dict:
    amount = int(amount)
    if amount <= 0:
        raise WalletError("amount must be more than zero")
    if _existing(conn, key, user_id):
        return {"duplicate": True, "balance": balance(conn, user_id)}
    cur = conn.execute("UPDATE wallets SET balance = balance - ? WHERE user_id=? AND balance >= ?",
                       (amount, user_id, amount))
    if cur.rowcount == 0:
        raise InsufficientFunds("not enough money in the wallet")
    conn.execute("INSERT INTO wallet_txns(user_id, amount, kind, idempotency_key, ref) VALUES (?,?,?,?,?)",
                 (user_id, -amount, "purchase", key, ref))
    return {"duplicate": False, "balance": balance(conn, user_id)}