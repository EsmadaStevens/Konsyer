import pytest

from app import attention, escrow, reschedule
from tests.test_disputes import book, client, conn, make_owner, voucher  # noqa: F401


def other_slot(conn, vid):
    rows = conn.execute("SELECT id FROM slots WHERE listing_id=? ORDER BY starts_at", (voucher(conn, vid)["listing_id"],)).fetchall()
    return rows[1]["id"]


def add_dispute(conn, vid, status="open", contested=None, resolved=None):
    conn.execute("INSERT INTO disputes(voucher_id, user_id, attempt, verdict, status, created_at, contested_at, resolved_at) "
                 "VALUES (?,1,1,'mismatch',?,?,?,?)", (vid, status, escrow._iso(escrow.now(conn)), contested, resolved))
    conn.commit()


def test_nothing_needs_attention_at_first(conn):
    assert attention.for_user(conn, 1) == {"count": 0, "items": []}


def test_a_seller_offer_is_an_action_for_the_customer(conn):
    vid = book(conn)
    make_owner(conn, vid)
    reschedule.offer(conn, 2, vid, other_slot(conn, vid))
    got = attention.for_user(conn, 1)
    assert got["count"] == 1 and got["items"][0]["kind"] == "action" and got["items"][0]["target"] == "payments"
    assert "new time" in got["items"][0]["text"]


def test_an_open_claim_is_an_action_for_the_seller_and_information_for_the_customer(conn):
    vid = book(conn)
    make_owner(conn, vid)
    add_dispute(conn, vid)
    seller = attention.for_user(conn, 2)
    assert seller["count"] == 1 and seller["items"][0]["target"] == "business"
    customer = attention.for_user(conn, 1)
    assert customer["count"] == 0 and customer["items"][0]["kind"] == "info" and "the seller" in customer["items"][0]["text"]


def test_a_contested_claim_says_it_is_with_konsyer(conn):
    vid = book(conn)
    make_owner(conn, vid)
    add_dispute(conn, vid, contested="2026-01-02T00:00:00Z")
    assert "Konsyer" in attention.for_user(conn, 1)["items"][0]["text"]
    assert attention.for_user(conn, 2)["count"] == 0   # nothing left for the seller to do


def test_a_decision_shows_for_two_days_then_goes_away(conn):
    vid = book(conn)
    stamp = escrow._iso(escrow.now(conn))
    add_dispute(conn, vid, status="refunded", resolved=stamp)
    assert "decided" in attention.for_user(conn, 1)["items"][0]["text"]
    escrow.advance(conn, 24 * 3)
    assert attention.for_user(conn, 1)["items"] == []


def test_the_endpoint(client, tmp_path):
    from tests.test_disputes import api_book
    api_book(tmp_path)
    r = client.get("/attention?user_id=1")
    assert r.status_code == 200 and r.json() == {"count": 0, "items": []}
