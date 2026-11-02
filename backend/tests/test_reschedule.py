import pytest

from app import disputes, escrow, reschedule, wallet
from app.escrow import EscrowError
from tests.test_disputes import api_book, book, client, conn, make_owner, voucher  # noqa: F401  (shared helpers)


def slots_of(conn, vid):
    return conn.execute("SELECT * FROM slots WHERE listing_id=? ORDER BY starts_at", (voucher(conn, vid)["listing_id"],)).fetchall()


def setup(conn):
    vid = book(conn)
    make_owner(conn, vid)
    return vid, slots_of(conn, vid)[1]["id"]


def test_the_seller_sees_other_times_with_room_but_not_the_current_one(conn):
    vid, other = setup(conn)
    got = [s["slot_id"] for s in reschedule.options(conn, 2, vid)["slots"]]
    assert got == [other]


def test_an_offer_changes_nothing_until_the_customer_answers(conn):
    vid, other = setup(conn)
    before = wallet.balance(conn, 1)
    reschedule.offer(conn, 2, vid, other)
    v = voucher(conn, vid)
    assert v["state"] == "held" and v["offer_slot_id"] == other and wallet.balance(conn, 1) == before
    view = escrow.list_for_user(conn, 1)[0]
    assert view["offer"]["slot_id"] == other and view["can_release"] is False and view["can_cancel"] is True


def test_accepting_moves_the_booking_and_the_clocks(conn):
    vid, other = setup(conn)
    old = voucher(conn, vid)
    reschedule.offer(conn, 2, vid, other)
    reschedule.accept(conn, 1, vid)
    v = voucher(conn, vid)
    new = conn.execute("SELECT * FROM slots WHERE id=?", (other,)).fetchone()
    assert v["state"] == "held" and v["starts_at"] == new["starts_at"] and v["offer_slot_id"] is None
    assert v["starts_at"] > old["starts_at"] and v["auto_release_at"] > old["auto_release_at"]
    assert v["free_cancel_until"] > old["free_cancel_until"]
    assert conn.execute("SELECT booked FROM slots WHERE id=?", (other,)).fetchone()[0] == 1
    b = conn.execute("SELECT slot_id FROM bookings WHERE id=?", (v["booking_id"],)).fetchone()
    assert b["slot_id"] == other


def old_slot(conn, vid):
    return slots_of(conn, vid)[0]["id"]


def test_the_old_slot_is_freed_when_the_customer_accepts(conn):
    vid, other = setup(conn)
    first = old_slot(conn, vid)
    assert conn.execute("SELECT booked FROM slots WHERE id=?", (first,)).fetchone()[0] == 1
    reschedule.offer(conn, 2, vid, other)
    reschedule.accept(conn, 1, vid)
    assert conn.execute("SELECT booked FROM slots WHERE id=?", (first,)).fetchone()[0] == 0


def test_declining_refunds_everything_even_after_the_free_cancellation_window(conn):
    vid, other = setup(conn)
    before = wallet.balance(conn, 1)
    reschedule.offer(conn, 2, vid, other)
    conn.execute("UPDATE vouchers SET free_cancel_until='2000-01-01T00:00:00Z' WHERE id=?", (vid,))
    conn.commit()
    reschedule.decline(conn, 1, vid)
    v = voucher(conn, vid)
    assert v["state"] == "refunded" and v["paid_amount"] == 0 and v["resolution"] == "declined_new_time"
    assert wallet.balance(conn, 1) == before + v["amount"]


def test_cancelling_while_an_offer_is_open_is_a_full_refund_not_a_late_fee(conn):
    vid, other = setup(conn)
    reschedule.offer(conn, 2, vid, other)
    conn.execute("UPDATE vouchers SET free_cancel_until='2000-01-01T00:00:00Z' WHERE id=?", (vid,))
    conn.commit()
    escrow.cancel(conn, 1, vid)
    v = voucher(conn, vid)
    assert v["state"] == "refunded" and v["paid_amount"] == 0


def test_an_unanswered_offer_never_pays_the_seller_and_lapses_into_a_refund(conn):
    vid, other = setup(conn)
    reschedule.offer(conn, 2, vid, other)
    v = voucher(conn, vid)
    escrow.advance(conn, 24 * 9)  # well past the original time and the 30 minute release point
    escrow.sweep(conn, 1)
    v = voucher(conn, vid)
    assert v["state"] == "refunded" and v["resolution"] == "offer_lapsed" and v["paid_amount"] == 0


def test_the_seller_cannot_confirm_while_an_offer_is_open(conn):
    vid, other = setup(conn)
    reschedule.offer(conn, 2, vid, other)
    conn.execute("UPDATE vouchers SET starts_at='2000-01-01T00:00:00Z' WHERE id=?", (vid,))
    conn.commit()
    with pytest.raises(EscrowError):
        escrow.merchant_confirm(conn, 2, vid)


def test_only_the_owner_can_offer_and_only_for_the_same_listing(conn):
    vid, other = setup(conn)
    with pytest.raises(EscrowError):
        reschedule.offer(conn, 1, vid, other)  # the customer is not the business
    foreign = conn.execute("SELECT id FROM slots WHERE listing_id != ? LIMIT 1", (voucher(conn, vid)["listing_id"],)).fetchone()
    with pytest.raises(EscrowError):
        reschedule.offer(conn, 2, vid, foreign["id"])
    with pytest.raises(EscrowError):
        reschedule.offer(conn, 2, vid, old_slot(conn, vid))  # the time they already have


def test_a_full_or_past_time_cannot_be_offered(conn):
    vid, other = setup(conn)
    conn.execute("UPDATE slots SET booked=capacity WHERE id=?", (other,))
    conn.commit()
    with pytest.raises(EscrowError):
        reschedule.offer(conn, 2, vid, other)
    conn.execute("UPDATE slots SET booked=0, starts_at='2000-01-01T00:00:00Z' WHERE id=?", (other,))
    conn.commit()
    with pytest.raises(EscrowError):
        reschedule.offer(conn, 2, vid, other)


def test_no_offer_while_the_customer_has_a_dispute_open(conn):
    vid, other = setup(conn)
    conn.execute("INSERT INTO disputes(voucher_id, user_id, attempt, verdict, status, created_at) "
                 "VALUES (?,1,1,'mismatch','open','2026-01-01T00:00:00Z')", (vid,))
    conn.commit()
    with pytest.raises(EscrowError):
        reschedule.offer(conn, 2, vid, other)


def test_the_customer_cannot_answer_an_offer_that_does_not_exist(conn):
    vid, _ = setup(conn)
    for fn in (reschedule.accept, reschedule.decline):
        with pytest.raises(EscrowError):
            fn(conn, 1, vid)


def test_the_merchant_list_says_who_can_offer_and_shows_a_pending_offer(conn):
    vid, other = setup(conn)
    mid = voucher(conn, vid)["merchant_id"]
    assert escrow.list_for_merchant(conn, 2, mid)[0]["can_offer_new_time"] is True
    reschedule.offer(conn, 2, vid, other)
    row = escrow.list_for_merchant(conn, 2, mid)[0]
    assert row["can_offer_new_time"] is False and row["offer"]["slot_id"] == other


def test_api_offer_then_accept(client, tmp_path):
    from app.db import get_conn
    vid = api_book(tmp_path)
    c = get_conn(str(tmp_path / "t.db"))
    c.execute("INSERT INTO users(id, name) VALUES (2, 'Owner')")
    make_owner(c, vid)
    other = slots_of(c, vid)[1]["id"]
    c.close()
    opts = client.get(f"/vouchers/{vid}/reschedule-options?user_id=2").json()["slots"]
    assert [o["slot_id"] for o in opts] == [other]
    assert client.post(f"/vouchers/{vid}/merchant-offer", json={"user_id": 1, "slot_id": other}).status_code == 404
    assert client.post(f"/vouchers/{vid}/merchant-offer", json={"user_id": 2, "slot_id": other}).status_code == 200
    seen = client.get("/vouchers?user_id=1").json()["vouchers"][0]
    assert seen["offer"]["slot_id"] == other
    assert client.post(f"/vouchers/{vid}/accept-offer", json={"user_id": 1}).status_code == 200
    assert client.get("/vouchers?user_id=1").json()["vouchers"][0]["offer"] is None
