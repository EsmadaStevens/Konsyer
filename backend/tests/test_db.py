import sqlite3
import pytest
from app.db import get_conn, init_db


def test_schema_and_idempotency_key_unique():
    c = get_conn(":memory:")
    init_db(c)
    c.execute("INSERT INTO users(id, name) VALUES (1,'t')")
    c.execute("INSERT INTO wallet_txns(user_id, amount, kind, idempotency_key) VALUES (1, 100, 'topup', 'k1')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO wallet_txns(user_id, amount, kind, idempotency_key) VALUES (1, 100, 'topup', 'k1')")


def test_wallet_cannot_go_negative():
    c = get_conn(":memory:")
    init_db(c)
    c.execute("INSERT INTO users(id, name) VALUES (1,'t')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO wallets(user_id, balance) VALUES (1, -1)")