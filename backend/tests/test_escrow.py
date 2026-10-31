from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import discovery, escrow, executor, main, wallet
from app.agent.tools import TOOLS, Tools
from app.db import get_conn, init_db
from app.escrow import EscrowError
from app.seed import seed
from app.timeutil import lusaka_today, parse_utc

TODAY = lusaka_today()
KEY = "test-admin-key"
K = 100  # ngwee per kwacha


@pytest.fixture
def conn():
    c = get_conn(":memory:")
    init_db(c)
    seed(c, TODAY)
    c.execute("INSERT INTO users(id, name) VALUES (2, 'Other user')")
    c.commit()
    yield c
    c.close()


def slot_of(conn, listing_id):
    return conn.execute("SELECT * FROM slots WHERE listing_id=? ORDER BY starts_at LIMIT 1", (listing_id,)).fetchone()


def book(conn, listing_id=1, qty=2):
    """Propose and approve a booking; returns (voucher_id, slot row)."""
    s = slot_of(conn, listing_id)
    out = Tools(conn, 1, TODAY).propose_action("book_listing", listing_id, s["id"], qty, plan_title="Test")
    r = executor.approve(conn, 1, out["action_id"], TODAY)
    assert r["ok"], r
    return r["voucher_id"], s


def voucher(conn, vid):
    return conn.execute("SELECT * FROM vouchers WHERE id=?", (vid,)).fetchone()


def iso_dt(iso):
    return parse_utc(iso)


def add_lead(conn, phone="0977123456", name="Glam Nails Lusaka"):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return conn.execute(
        "INSERT INTO web_leads(search_key, name, area, phone, price_text, price_for, source_url, platform, "
        "corroborated, is_sample, created_at) VALUES ('k', ?, 'Kabulonga', ?, NULL, NULL, 'https://example.com/p', "
        "'website', 0, 0, ?)", (name, phone, stamp)).lastrowid


# ---------- booking vouchers (verified seller) ----------
def test_booking_creates_held_voucher_with_policy_snapshot(conn):
    before = wallet.balance(conn, 1)
    vid, s = book(conn)
    v = voucher(conn, vid)
    assert v["state"] == "held" and v["payee_verified"] == 1 and v["amount"] == 30000
    assert wallet.balance(conn, 1) == before - 30000
    assert escrow.held_total(conn, 1) == 30000
    assert iso_dt(v["free_cancel_until"]) == iso_dt(s["starts_at"]) - timedelta(hours=24)
    assert v["forfeit_pct"] == 50
    assert iso_dt(v["auto_release_at"]) == iso_dt(s["ends_at"]) + timedelta(hours=24)


def test_free_cancel_gives_everything_back_and_frees_the_slot(conn):
    vid, s = book(conn)
    before = wallet.balance(conn, 1)
    booked_before = slot_of(conn, 1)["booked"]
    at = iso_dt(s["starts_at"]) - timedelta(hours=30)
    r = escrow.cancel(conn, 1, vid, at)
    assert r["voucher"]["state"] == "refunded"
    assert wallet.balance(conn, 1) == before + 30000
    assert slot_of(conn, 1)["booked"] == booked_before - 2
    assert conn.execute("SELECT status FROM bookings").fetchone()["status"] == "cancelled"
    assert escrow.held_total(conn, 1) == 0


def test_late_cancel_seller_keeps_the_policy_share(conn):
    vid, s = book(conn)
    before = wallet.balance(conn, 1)
    r = escrow.cancel(conn, 1, vid, iso_dt(s["starts_at"]) - timedelta(hours=2))
    v = r["voucher"]
    assert v["state"] == "forfeited" and v["paid_amount"] == 15000 and v["refunded_amount"] == 15000
    assert wallet.balance(conn, 1) == before + 15000
    assert voucher(conn, vid)["payout_ref"].startswith("simpay_")


def test_user_release_pays_seller_once(conn):
    vid, s = book(conn)
    before = wallet.balance(conn, 1)
    r = escrow.release(conn, 1, vid, at=iso_dt(s["starts_at"]))
    assert r["voucher"]["state"] == "released" and r["voucher"]["simulated"] is True
    assert wallet.balance(conn, 1) == before  # the money had already left the balance
    with pytest.raises(EscrowError) as e:
        escrow.release(conn, 1, vid)
    assert e.value.code == "conflict"
    with pytest.raises(EscrowError):
        escrow.cancel(conn, 1, vid)


def test_auto_release_after_24h_but_not_before(conn):
    vid, s = book(conn)
    end = iso_dt(s["ends_at"])
    assert escrow.sweep(conn, 1, end + timedelta(hours=23)) == 0
    assert voucher(conn, vid)["state"] == "held"
    assert escrow.sweep(conn, 1, end + timedelta(hours=25)) == 1
    v = voucher(conn, vid)
    assert v["state"] == "released" and v["resolution"] == "auto_released"


def test_other_user_cannot_touch_a_voucher(conn):
    vid, _ = book(conn)
    for fn in (lambda: escrow.cancel(conn, 2, vid), lambda: escrow.release(conn, 2, vid),
               lambda: escrow.report(conn, 2, vid, "looks_like_scam")):
        with pytest.raises(EscrowError) as e:
            fn()
        assert e.value.code == "not_found"
    assert voucher(conn, vid)["state"] == "held"


def test_report_verified_no_show_only_after_start(conn):
    vid, s = book(conn)
    start = iso_dt(s["starts_at"])
    with pytest.raises(EscrowError) as e:
        escrow.report(conn, 1, vid, "not_delivered", start - timedelta(hours=1))
    assert e.value.code == "conflict"
    with pytest.raises(EscrowError) as e:
        escrow.report(conn, 1, vid, "because I said so", start)
    assert e.value.code == "invalid"
    before = wallet.balance(conn, 1)
    r = escrow.report(conn, 1, vid, "not_delivered", start + timedelta(hours=1))
    assert r["voucher"]["state"] == "refunded" and wallet.balance(conn, 1) == before + 30000


# ---------- seller actions ----------
def make_owner(conn, listing_id=1, owner=2):
    mid = conn.execute("SELECT merchant_id FROM listings WHERE id=?", (listing_id,)).fetchone()[0]
    conn.execute("UPDATE merchants SET owner_user_id=? WHERE id=?", (owner, mid))
    conn.commit()
    return mid


def test_merchant_confirm_only_owner_and_only_after_start(conn):
    vid, s = book(conn)
    make_owner(conn)
    start = iso_dt(s["starts_at"])
    with pytest.raises(EscrowError) as e:
        escrow.merchant_confirm(conn, 1, vid, start)  # user 1 does not own the business
    assert e.value.code == "not_found"
    with pytest.raises(EscrowError) as e:
        escrow.merchant_confirm(conn, 2, vid, start - timedelta(hours=1))
    assert e.value.code == "conflict"
    r = escrow.merchant_confirm(conn, 2, vid, start + timedelta(minutes=5))
    assert r["voucher"]["state"] == "released" and voucher(conn, vid)["resolution"] == "released_by_merchant"


def test_merchant_cancel_refunds_customer_in_full(conn):
    vid, s = book(conn)
    make_owner(conn)
    before = wallet.balance(conn, 1)
    r = escrow.merchant_cancel(conn, 2, vid, iso_dt(s["starts_at"]) - timedelta(hours=1))  # even inside the late window
    assert r["voucher"]["state"] == "refunded" and wallet.balance(conn, 1) == before + 30000


def test_merchant_list_shows_only_their_verified_bookings(conn):
    vid, _ = book(conn)
    mid = make_owner(conn)
    rows = escrow.list_for_merchant(conn, 2, mid)
    assert [r["id"] for r in rows] == [vid]
    with pytest.raises(EscrowError):
        escrow.list_for_merchant(conn, 1, mid)


# ---------- unverified booking payee (flyer listing) ----------
def test_unverified_booking_is_never_auto_paid_and_cancels_free(conn):
    vid, s = book(conn, listing_id=5, qty=1)  # Sample Afrobeats Night: flyer, unverified
    v = voucher(conn, vid)
    assert v["payee_verified"] == 0 and v["auto_release_at"] is None and v["forfeit_pct"] == 0
    end = iso_dt(s["ends_at"])
    assert escrow.sweep(conn, 1, end + timedelta(days=3)) == 0  # not paid, not yet expired
    assert voucher(conn, vid)["state"] == "held"
    before = wallet.balance(conn, 1)
    assert escrow.sweep(conn, 1, end + timedelta(days=8)) == 1  # expired: back to the user
    assert voucher(conn, vid)["state"] == "refunded" and wallet.balance(conn, 1) == before + 15000


def test_unverified_booking_release_needs_a_confirmed_number(conn):
    vid, _ = book(conn, listing_id=5, qty=1)
    with pytest.raises(EscrowError) as e:
        escrow.release(conn, 1, vid)
    assert e.value.code == "invalid"


# ---------- outside leads ----------
def test_lead_hold_debits_and_holds(conn):
    lid = add_lead(conn)
    before = wallet.balance(conn, 1)
    r = escrow.create_lead_hold(conn, 1, lid, 25000, "French tips", "k1", TODAY)
    assert r["ok"] and r["outcome"] == "held"
    v = r["voucher"]
    assert v["state"] == "held" and v["payee_verified"] is False and v["needs_payout_number"] is True
    assert v["label"] == "French tips to Glam Nails Lusaka" and v["payee_phone"] == "+260977123456"
    assert wallet.balance(conn, 1) == before - 25000 and escrow.held_total(conn, 1) == 25000
    assert wallet.spent_today(conn, 1, TODAY) == 25000
    kinds = [t["kind"] for t in conn.execute("SELECT kind FROM wallet_txns WHERE user_id=1")]
    assert "hold" in kinds


def test_lead_hold_is_idempotent(conn):
    lid = add_lead(conn)
    a = escrow.create_lead_hold(conn, 1, lid, 25000, None, "same", TODAY)
    b = escrow.create_lead_hold(conn, 1, lid, 25000, None, "same", TODAY)
    assert b["outcome"] == "already_held" and a["voucher"]["id"] == b["voucher"]["id"]
    assert conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0] == 1
    assert escrow.held_total(conn, 1) == 25000


@pytest.mark.parametrize("amount", [0, -5, 50, 10.5, True, "300", None])
def test_lead_hold_rejects_bad_amounts(conn, amount):
    lid = add_lead(conn)
    with pytest.raises(EscrowError) as e:
        escrow.create_lead_hold(conn, 1, lid, amount, None, "x", TODAY)
    assert e.value.code == "invalid"
    assert escrow.held_total(conn, 1) == 0


def test_lead_hold_blocked_by_caps_and_balance(conn):
    lid = add_lead(conn)
    before = wallet.balance(conn, 1)
    r = escrow.create_lead_hold(conn, 1, lid, 150000, None, "big", TODAY)  # over the K1,000 per-purchase cap
    assert r["ok"] is False and r["rule"] == "per_purchase_cap"
    assert wallet.balance(conn, 1) == before and conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0] == 0
    for i in range(3):  # three K1,000 holds reach the K3,000 daily cap
        assert escrow.create_lead_hold(conn, 1, lid, 100000, None, f"d{i}", TODAY)["ok"]
    r = escrow.create_lead_hold(conn, 1, lid, 100, None, "d4", TODAY)
    assert r["ok"] is False and r["rule"] == "daily_cap"
    conn.execute("UPDATE wallets SET balance=1000 WHERE user_id=1")
    conn.execute("UPDATE profiles SET daily_cap=100000000, per_purchase_cap=100000000 WHERE user_id=1")
    conn.commit()
    r = escrow.create_lead_hold(conn, 1, lid, 5000, None, "poor", TODAY)
    assert r["ok"] is False and r["rule"] == "insufficient_funds"


def test_lead_hold_needs_a_live_lead(conn):
    with pytest.raises(EscrowError) as e:
        escrow.create_lead_hold(conn, 1, 999, 25000, None, "x", TODAY)
    assert e.value.code == "not_found"
    lid = add_lead(conn)
    conn.execute("UPDATE web_leads SET created_at='2020-01-01T00:00:00+00:00'")
    conn.commit()
    with pytest.raises(EscrowError):
        escrow.create_lead_hold(conn, 1, lid, 25000, None, "y", TODAY)


def test_lead_cancel_is_a_full_refund_and_lowers_spent_today(conn):
    lid = add_lead(conn)
    before = wallet.balance(conn, 1)
    vid = escrow.create_lead_hold(conn, 1, lid, 25000, None, "c", TODAY)["voucher"]["id"]
    r = escrow.cancel(conn, 1, vid)
    assert r["voucher"]["state"] == "refunded" and wallet.balance(conn, 1) == before
    assert wallet.spent_today(conn, 1, TODAY) == 0


def test_lead_report_refunds_any_time(conn):
    lid = add_lead(conn)
    vid = escrow.create_lead_hold(conn, 1, lid, 25000, None, "r", TODAY)["voucher"]["id"]
    r = escrow.report(conn, 1, vid, "looks_like_scam")
    assert r["voucher"]["state"] == "refunded" and voucher(conn, vid)["resolution"] == "reported_looks_like_scam"


def test_lead_release_requires_confirmed_validated_number(conn):
    lid = add_lead(conn, phone="0977123456")
    vid = escrow.create_lead_hold(conn, 1, lid, 25000, None, "rel", TODAY)["voucher"]["id"]
    with pytest.raises(EscrowError) as e:
        escrow.release(conn, 1, vid)  # no number
    assert e.value.code == "invalid"
    with pytest.raises(EscrowError) as e:
        escrow.release(conn, 1, vid, "12345", "12345")  # not a Zambian mobile number
    assert e.value.code == "invalid"
    with pytest.raises(EscrowError) as e:
        escrow.release(conn, 1, vid, "0977123456")  # not confirmed on screen
    assert e.value.code == "confirm"
    with pytest.raises(EscrowError) as e:
        escrow.release(conn, 1, vid, "0977123456", "0966000000")  # confirmation does not match
    assert e.value.code == "confirm"
    assert voucher(conn, vid)["state"] == "held"
    r = escrow.release(conn, 1, vid, "0977 123 456", "+260977123456")  # same as the number on file: no warning
    assert r["voucher"]["state"] == "released" and voucher(conn, vid)["payout_ref"].startswith("simpay_")


def test_lead_release_to_a_different_number_needs_explicit_acceptance(conn):
    lid = add_lead(conn, phone="0977123456")
    vid = escrow.create_lead_hold(conn, 1, lid, 25000, None, "mm", TODAY)["voucher"]["id"]
    with pytest.raises(EscrowError) as e:
        escrow.release(conn, 1, vid, "0966555444", "0966555444")
    assert e.value.code == "confirm" and "different" in e.value.message
    assert voucher(conn, vid)["state"] == "held"
    r = escrow.release(conn, 1, vid, "0966555444", "0966555444", accept_warning=True)
    assert r["voucher"]["state"] == "released"


def test_unverified_money_returns_after_expiry_if_user_does_nothing(conn):
    lid = add_lead(conn)
    vid = escrow.create_lead_hold(conn, 1, lid, 25000, None, "e", TODAY)["voucher"]["id"]
    before = wallet.balance(conn, 1)
    assert escrow.sweep(conn, 1, escrow.now(conn) + timedelta(days=6)) == 0
    assert escrow.sweep(conn, 1, escrow.now(conn) + timedelta(days=8)) == 1
    assert voucher(conn, vid)["resolution"] == "expired" and wallet.balance(conn, 1) == before + 25000


# ---------- number checks ----------
def test_check_number():
    c = escrow.check_number("+260 977 123 456", "0977123456")
    assert c["number"] == "260977123456" and c["warning"] is None and "Airtel" in c["network_note"]
    assert "different" in escrow.check_number("0966111222", "0977123456")["warning"]
    assert "No number" in escrow.check_number("0966111222", None)["warning"]
    for bad in ("", None, "abc", "0211234567", "097712345", "09771234567"):
        with pytest.raises(EscrowError):
            escrow.check_number(bad)


# ---------- the model can never move money ----------
def test_agent_has_no_tool_that_holds_or_releases_money():
    names = " ".join(t["function"]["name"] for t in TOOLS).lower()
    for word in ("hold", "release", "voucher", "escrow", "refund", "payout", "pay_"):
        assert word not in names


def test_demo_clock_moves_forward(conn):
    a = escrow.now(conn)
    escrow.advance(conn, 25)
    assert escrow.now(conn) - a >= timedelta(hours=24, minutes=59)
    for bad in (0, -1, 10_000, 1.5, True):
        with pytest.raises(EscrowError):
            escrow.advance(conn, bad)


# ---------- API ----------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KONSYER_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("KONSYER_ADMIN_KEY", KEY)
    return TestClient(main.app)


def api_lead(client, tmp_path):
    c = get_conn(str(tmp_path / "t.db"))
    init_db(c)
    seed(c, TODAY)
    lid = add_lead(c)
    c.commit()
    c.close()
    return lid


def test_api_hold_check_release_flow(client, tmp_path):
    lid = api_lead(client, tmp_path)
    w0 = client.get("/wallet?user_id=1").json()
    r = client.post(f"/leads/{lid}/hold", json={"amount_ngwee": 25000, "what": "French tips"}).json()
    assert r["ok"] and r["voucher"]["state"] == "held"
    vid = r["voucher"]["id"]
    w1 = client.get("/wallet?user_id=1").json()
    assert w1["held"] == 25000 and w1["balance"] == w0["balance"] - 25000
    assert client.get("/vouchers?user_id=1").json()["vouchers"][0]["id"] == vid
    chk = client.post(f"/vouchers/{vid}/payout-check", json={"payout_number": "0966555444"}).json()
    assert chk["number"] == "260966555444" and chk["warning"]
    bad = client.post(f"/vouchers/{vid}/release", json={"payout_number": "0966555444"})
    assert bad.status_code == 422
    ok = client.post(f"/vouchers/{vid}/release", json={"payout_number": "0966555444",
                                                       "confirmed_number": "0966555444", "accept_warning": True})
    assert ok.status_code == 200 and ok.json()["voucher"]["state"] == "released"
    assert client.get("/wallet?user_id=1").json()["held"] == 0
    assert client.post(f"/vouchers/{vid}/cancel", json={}).status_code == 409


def test_api_hold_over_cap_is_blocked_not_an_error(client, tmp_path):
    lid = api_lead(client, tmp_path)
    r = client.post(f"/leads/{lid}/hold", json={"amount_ngwee": 200_000}).json()
    assert r["ok"] is False and r["rule"] == "per_purchase_cap"


def test_api_unknown_voucher_and_missing_lead(client):
    assert client.post("/vouchers/999/cancel", json={}).status_code == 404
    assert client.post("/leads/999/hold", json={"amount_ngwee": 5000}).status_code == 404


def test_api_advance_time_needs_admin_key(client):
    assert client.post("/admin/advance-time", json={"hours": 25}).status_code == 403
    ok = client.post("/admin/advance-time", json={"hours": 25}, headers={"X-Admin-Key": KEY})
    assert ok.status_code == 200 and ok.json()["offset_hours"] == 25