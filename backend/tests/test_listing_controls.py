import pytest

from app import executor, merchants
from app.agent.tools import Tools
from app.db import get_conn, init_db
from app.merchants import MerchantError
from app.seed import seed
from app.timeutil import lusaka_today

TODAY = lusaka_today()


@pytest.fixture
def conn():
    c = get_conn(":memory:")
    init_db(c)
    seed(c, TODAY)
    c.execute("INSERT INTO users(id, name) VALUES (2, 'Owner')")
    c.execute("INSERT INTO users(id, name) VALUES (3, 'Other')")
    c.execute("INSERT INTO merchants(id, name, owner_user_id, verified) VALUES (9, 'Shop', 2, 1)")
    c.execute("INSERT INTO listings(id, merchant_id, type, name, description, price, status, source, tags) "
              "VALUES (90, 9, 'service', 'Braids', 'x', 1000, 'verified', 'merchant', '[\"braids\"]')")
    c.execute("INSERT INTO slots(listing_id, starts_at, capacity) VALUES (90, '2099-01-01T08:00:00Z', 2)")
    c.commit()
    yield c
    c.close()


def slot(day, cap=3):
    return {"date": day, "start_time": "10:00", "end_time": "12:00", "capacity": cap}


def test_the_owner_can_add_times_to_a_live_listing(conn):
    day = TODAY.replace(year=TODAY.year + 1).isoformat()
    out = merchants.add_slots(conn, 2, 90, [slot(day), slot(day, 5)], TODAY)
    assert out["added"] == 2
    assert conn.execute("SELECT COUNT(*) FROM slots WHERE listing_id=90").fetchone()[0] == 3


def test_added_times_are_checked_like_new_ones(conn):
    with pytest.raises(MerchantError):
        merchants.add_slots(conn, 2, 90, [slot("2001-01-01")], TODAY)       # in the past
    with pytest.raises(MerchantError):
        merchants.add_slots(conn, 2, 90, [], TODAY)
    with pytest.raises(MerchantError):
        merchants.add_slots(conn, 2, 90, [slot("not-a-date")], TODAY)


def test_only_the_owner_can_add_times_or_pause(conn):
    day = TODAY.replace(year=TODAY.year + 1).isoformat()
    with pytest.raises(MerchantError):
        merchants.add_slots(conn, 3, 90, [slot(day)], TODAY)
    with pytest.raises(MerchantError):
        merchants.set_paused(conn, 3, 90, True)


def test_a_paused_listing_disappears_for_customers_and_cannot_be_booked(conn):
    t = Tools(conn, 1, TODAY)
    assert any(l["listing_id"] == 90 for l in t.search_listings(query="braids")["listings"])
    merchants.set_paused(conn, 2, 90, True)
    assert not any(l["listing_id"] == 90 for l in t.search_listings(query="braids")["listings"])
    assert t.get_listing(90) == {"error": "listing not found"}
    sid = conn.execute("SELECT id FROM slots WHERE listing_id=90").fetchone()["id"]
    assert "error" in t.propose_action("book_listing", 90, sid, 1, plan_title="x")
    merchants.set_paused(conn, 2, 90, False)
    assert any(l["listing_id"] == 90 for l in t.search_listings(query="braids")["listings"])


def test_pausing_after_a_proposal_blocks_the_approval(conn):
    t = Tools(conn, 1, TODAY)
    sid = conn.execute("SELECT id FROM slots WHERE listing_id=90").fetchone()["id"]
    a = t.propose_action("book_listing", 90, sid, 1, plan_title="x")["action_id"]
    merchants.set_paused(conn, 2, 90, True)
    out = executor.approve(conn, 1, a, TODAY)
    assert out["ok"] is False
    assert conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0] == 0


def test_pausing_does_not_touch_a_booking_that_already_exists(conn):
    t = Tools(conn, 1, TODAY)
    sid = conn.execute("SELECT id FROM slots WHERE listing_id=90").fetchone()["id"]
    a = t.propose_action("book_listing", 90, sid, 1, plan_title="x")["action_id"]
    assert executor.approve(conn, 1, a, TODAY)["ok"] is True
    merchants.set_paused(conn, 2, 90, True)
    assert conn.execute("SELECT state FROM vouchers").fetchone()["state"] == "held"


def test_the_owner_list_says_which_listings_are_paused(conn):
    merchants.set_paused(conn, 2, 90, True)
    row = [l for l in merchants.get_merchant(conn, 2, 9)["listings"] if l["id"] == 90][0]
    assert row["paused"] == 1
