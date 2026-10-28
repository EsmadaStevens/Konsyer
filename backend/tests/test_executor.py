import pytest

from app import executor, wallet
from app.agent.tools import Tools
from app.db import get_conn, init_db
from app.seed import seed
from app.timeutil import lusaka_today

TODAY = lusaka_today()


@pytest.fixture
def conn():
    c = get_conn(":memory:")
    init_db(c)
    seed(c, TODAY)
    yield c
    c.close()


def slot_of(conn, listing_id):
    return conn.execute("SELECT id FROM slots WHERE listing_id=? ORDER BY starts_at LIMIT 1",
                        (listing_id,)).fetchone()["id"]


def propose(conn, tools, listing_id, qty=5):
    return tools.propose_action("book_listing", listing_id, slot_of(conn, listing_id), qty, plan_title="Test")


def status_of(conn, aid):
    return conn.execute("SELECT status FROM pending_actions WHERE id=?", (aid,)).fetchone()["status"]


def test_approve_books_and_debits(conn):
    out = propose(conn, Tools(conn, 1, TODAY), 1)
    before = wallet.balance(conn, 1)
    r = executor.approve(conn, 1, out["action_id"], TODAY)
    assert r["ok"] and r["outcome"] == "executed"
    assert wallet.balance(conn, 1) == before - 75000
    assert r["booking"]["qr_token"]
    assert status_of(conn, out["action_id"]) == "executed"
    assert conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 1
    events = [e["event"] for e in conn.execute("SELECT event FROM audit_log WHERE pending_action_id=?",
                                               (out["action_id"],))]
    assert "approved_by_user" in events and "executed" in events


def test_approving_twice_charges_once(conn):
    out = propose(conn, Tools(conn, 1, TODAY), 1)
    executor.approve(conn, 1, out["action_id"], TODAY)
    bal = wallet.balance(conn, 1)
    r = executor.approve(conn, 1, out["action_id"], TODAY)
    assert r["outcome"] == "already_executed"
    assert wallet.balance(conn, 1) == bal


def test_policy_rechecked_when_wallet_was_spent_meanwhile(conn):
    out = propose(conn, Tools(conn, 1, TODAY), 1)  # K750, pending
    wallet.debit(conn, 1, wallet.balance(conn, 1) - 20000, "drain", "test")
    conn.commit()
    r = executor.approve(conn, 1, out["action_id"], TODAY)
    assert not r["ok"] and r["outcome"] == "blocked_by_policy" and r["rule"] == "insufficient_funds"
    assert wallet.balance(conn, 1) == 20000
    assert status_of(conn, out["action_id"]) == "denied"
    assert conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 0


def test_daily_cap_enforced_at_approval(conn):
    conn.execute("UPDATE profiles SET daily_cap=100000 WHERE user_id=1")
    conn.commit()
    t = Tools(conn, 1, TODAY)
    a = propose(conn, t, 1)["action_id"]  # K750
    b = propose(conn, t, 4)["action_id"]  # K900
    assert executor.approve(conn, 1, a, TODAY)["ok"]
    r = executor.approve(conn, 1, b, TODAY)
    assert not r["ok"] and r["rule"] == "daily_cap"
    assert status_of(conn, b) == "denied"


def test_no_spots_left_fails_without_charging(conn):
    out = propose(conn, Tools(conn, 1, TODAY), 1)
    conn.execute("UPDATE slots SET booked = capacity - 2 WHERE id=?", (slot_of(conn, 1),))
    conn.commit()
    bal = wallet.balance(conn, 1)
    r = executor.approve(conn, 1, out["action_id"], TODAY)
    assert r["outcome"] == "failed"
    assert wallet.balance(conn, 1) == bal
    assert status_of(conn, out["action_id"]) == "failed"


def test_other_user_cannot_approve(conn):
    out = propose(conn, Tools(conn, 1, TODAY), 1)
    with pytest.raises(executor.ExecError):
        executor.approve(conn, 2, out["action_id"], TODAY)


def test_action_denied_at_proposal_cannot_be_approved(conn):
    out = propose(conn, Tools(conn, 1, TODAY), 1, qty=7)  # K1,050 is over the K1,000 cap
    assert out["decision"] == "DENY"
    with pytest.raises(executor.ExecError):
        executor.approve(conn, 1, out["action_id"], TODAY)


def test_deny_charges_nothing(conn):
    out = propose(conn, Tools(conn, 1, TODAY), 1)
    bal = wallet.balance(conn, 1)
    r = executor.deny(conn, 1, out["action_id"])
    assert r["ok"] and wallet.balance(conn, 1) == bal
    assert status_of(conn, out["action_id"]) == "denied"


def test_ride_home_executes(conn):
    out = propose(conn, Tools(conn, 1, TODAY), 4)
    ride_id = out["ride_home_auto_added"]["action_id"]
    bal = wallet.balance(conn, 1)
    r = executor.approve(conn, 1, ride_id, TODAY)
    assert r["ok"]
    assert wallet.balance(conn, 1) < bal
    assert conn.execute("SELECT COUNT(*) FROM plan_items WHERE kind='ride_home'").fetchone()[0] == 1


def test_topup_is_idempotent_and_validated(conn):
    bal = wallet.balance(conn, 1)
    wallet.topup(conn, 1, 50000, "k1")
    wallet.topup(conn, 1, 50000, "k1")
    conn.commit()
    assert wallet.balance(conn, 1) == bal + 50000
    with pytest.raises(wallet.WalletError):
        wallet.topup(conn, 1, 0, "k2")

def test_declining_dinner_cancels_ride_but_keeps_other_items(conn):
    t = Tools(conn, 1, TODAY)
    karting = propose(conn, t, 1)["action_id"]
    dinner = propose(conn, t, 4)
    ride_id = dinner["ride_home_auto_added"]["action_id"]
    executor.deny(conn, 1, dinner["action_id"])
    assert status_of(conn, ride_id) == "denied"
    assert status_of(conn, karting) == "pending"