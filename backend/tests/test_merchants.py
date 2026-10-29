from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import main, merchants
from app.agent.tools import TOOLS, Tools
from app.db import get_conn, init_db
from app.seed import seed
from app.timeutil import lusaka_today, to_local

TODAY = lusaka_today()
DAY = (TODAY + timedelta(days=3)).isoformat()
KEY = "test-admin-key"


@pytest.fixture
def conn():
    c = get_conn(":memory:")
    init_db(c)
    seed(c, TODAY)
    c.execute("INSERT INTO users(id, name) VALUES (2, 'Business owner')")
    c.commit()
    yield c
    c.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KONSYER_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("KONSYER_ADMIN_KEY", KEY)
    return TestClient(main.app)


def listing_fields(**over):
    f = {"title": "Quiet Piano Bar", "listing_type": "dining", "description": "Live piano.", "area": "Kabulonga",
         "price_ngwee": 12000, "serves_alcohol": True, "tags": ["music", "dinner"],
         "slots": [{"date": DAY, "start_time": "19:00", "end_time": "21:00", "capacity": 10}]}
    f.update(over)
    return f


def signup(conn, **over):
    m = merchants.register(conn, 2, {"name": "Piano Co", "area": "Kabulonga", "whatsapp": "0971234567", **over})
    return m["merchant_id"]


def tools(conn):
    return Tools(conn, 1, TODAY)


def test_signup_creates_claimed_listing_with_real_capacity(conn):
    mid = signup(conn)
    r = merchants.add_listing(conn, 2, mid, listing_fields(), TODAY)
    row = conn.execute("SELECT * FROM listings WHERE id=?", (r["listing_id"],)).fetchone()
    assert (row["status"], row["source"], row["trust_score"], row["is_sample"]) == ("claimed", "merchant", 0.3, 0)
    assert row["contact_whatsapp"] == "260971234567"
    slot = conn.execute("SELECT * FROM slots WHERE listing_id=?", (row["id"],)).fetchone()
    assert slot["capacity"] == 10 and slot["booked"] == 0
    assert conn.execute("SELECT verified FROM merchants WHERE id=?", (mid,)).fetchone()["verified"] == 0


def test_claimed_listing_is_invisible_and_unbookable_for_the_agent(conn):
    mid = signup(conn)
    lid = merchants.add_listing(conn, 2, mid, listing_fields(), TODAY)["listing_id"]
    sid = conn.execute("SELECT id FROM slots WHERE listing_id=?", (lid,)).fetchone()["id"]
    t = tools(conn)
    assert all(x["listing_id"] != lid for x in t.search_listings(query="piano bar")["listings"])
    assert t.get_listing(lid) == {"error": "listing not found"}
    r = t.propose_action("book_listing", lid, sid, 2, "x", 100000)
    assert "awaiting verification" in r["error"]
    assert conn.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0] == 0


def test_no_agent_tool_can_verify(conn):
    assert not any("verif" in t["function"]["name"] for t in TOOLS)


def test_verify_needs_the_admin_key(client, monkeypatch):
    uid = 1
    mid = client.post("/merchants", json={"user_id": uid, "name": "Piano Co"}).json()["merchant_id"]
    lid = client.post(f"/merchants/{mid}/listings", json={"user_id": uid, **listing_fields()}).json()["listing_id"]
    assert client.post(f"/admin/merchants/{mid}/verify").status_code == 403
    assert client.post(f"/admin/merchants/{mid}/verify", headers={"X-Admin-Key": "wrong"}).status_code == 403
    monkeypatch.delenv("KONSYER_ADMIN_KEY")
    assert client.post(f"/admin/merchants/{mid}/verify", headers={"X-Admin-Key": KEY}).status_code == 403
    monkeypatch.setenv("KONSYER_ADMIN_KEY", KEY)
    r = client.post(f"/admin/merchants/{mid}/verify", headers={"X-Admin-Key": KEY})
    assert r.status_code == 200 and r.json()["listings_verified"] == 1
    assert client.post("/admin/merchants/999/verify", headers={"X-Admin-Key": KEY}).status_code == 404
    got = client.get(f"/merchants/{mid}", params={"user_id": uid}).json()
    assert got["verified"] is True and got["listings"][0]["id"] == lid and got["listings"][0]["status"] == "verified"


def test_verified_listing_is_found_trusted_and_ranked_before_a_better_keyword_match(conn):
    mid = signup(conn)
    lid = merchants.add_listing(conn, 2, mid, listing_fields(), TODAY)["listing_id"]
    merchants.verify(conn, mid)
    t = tools(conn)
    found = t.search_listings(query="music dance nightlife")["listings"]
    names = [x["name"] for x in found]
    # the flyer-sourced Afrobeats Night matches 3 keywords, the verified piano bar only 1: verified still first
    assert names.index("Quiet Piano Bar") < names.index("Sample Afrobeats Night")
    assert found[0]["verified"] is True
    trust = t.check_trust(lid)
    assert trust["status"] == "verified" and trust["payment_needs_user_approval"] is False


def test_ranking_order_verified_then_flyer_then_web_lead(conn):
    found = tools(conn).search_listings()["listings"]
    tiers = [0 if x["verified"] else 2 if x["source"] == "web_lead" else 1 for x in found]
    assert tiers == sorted(tiers)
    assert found[-1]["source"] == "web_lead"


def test_web_leads_can_never_be_proposed(conn):
    t = tools(conn)
    nail = conn.execute("SELECT id FROM listings WHERE source='web_lead'").fetchone()["id"]
    assert conn.execute("SELECT COUNT(*) FROM slots WHERE listing_id=?", (nail,)).fetchone()[0] == 0
    slot = conn.execute("SELECT id FROM slots LIMIT 1").fetchone()["id"]
    assert "web lead" in t.propose_action("book_listing", nail, slot, 1, "x", 100000)["error"]


def test_only_the_owner_can_add_listings(conn):
    mid = signup(conn)
    with pytest.raises(merchants.MerchantError) as e:
        merchants.add_listing(conn, 1, mid, listing_fields(), TODAY)
    assert e.value.code == "forbidden"


@pytest.mark.parametrize("over,msg", [
    ({"title": " "}, "Title"),
    ({"listing_type": "spaceship"}, "Type"),
    ({"price_ngwee": None}, "Price"),
    ({"price_ngwee": 0}, "Price"),
    ({"price_ngwee": 9_999_999}, "Price"),
    ({"slots": []}, "slots"),
    ({"slots": [{"date": (TODAY - timedelta(days=1)).isoformat(), "start_time": "19:00", "capacity": 5}]}, "past"),
    ({"slots": [{"date": DAY, "start_time": "7pm", "capacity": 5}]}, "start time"),
    ({"slots": [{"date": DAY, "start_time": "19:00", "capacity": 0}]}, "capacity"),
    ({"slots": [{"date": DAY, "start_time": "19:00", "end_time": "19:00", "capacity": 5}]}, "differ"),
])
def test_validation_rejects_bad_input_and_writes_nothing(conn, over, msg):
    mid = signup(conn)
    before = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    with pytest.raises(merchants.MerchantError) as e:
        merchants.add_listing(conn, 2, mid, listing_fields(**over), TODAY)
    assert e.value.code == "invalid" and msg.lower() in e.value.message.lower()
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == before


def test_signup_validation(conn):
    with pytest.raises(merchants.MerchantError):
        merchants.register(conn, 2, {"name": "  "})
    with pytest.raises(merchants.MerchantError):
        merchants.register(conn, 2, {"name": "X", "whatsapp": "12"})
    with pytest.raises(merchants.MerchantError) as e:
        merchants.register(conn, 99, {"name": "X"})
    assert e.value.code == "not_found"


def test_overnight_slot_ends_next_day(conn):
    mid = signup(conn)
    lid = merchants.add_listing(conn, 2, mid, listing_fields(slots=[
        {"date": DAY, "start_time": "21:00", "end_time": "02:00", "capacity": 50}]), TODAY)["listing_id"]
    s = conn.execute("SELECT * FROM slots WHERE listing_id=?", (lid,)).fetchone()
    assert to_local(s["ends_at"]).date() == to_local(s["starts_at"]).date() + timedelta(days=1)


def test_verify_is_repeatable_and_picks_up_new_listings(conn):
    mid = signup(conn)
    merchants.add_listing(conn, 2, mid, listing_fields(), TODAY)
    assert merchants.verify(conn, mid)["listings_verified"] == 1
    merchants.add_listing(conn, 2, mid, listing_fields(title="Second Room"), TODAY)
    st = conn.execute("SELECT status FROM listings WHERE name='Second Room'").fetchone()["status"]
    assert st == "claimed"   # a verified merchant's NEW listing is not auto-verified
    assert merchants.verify(conn, mid) == {"merchant_id": mid, "verified": True, "listings_verified": 1}
    events = [r["event"] for r in conn.execute("SELECT event FROM audit_log")]
    assert events.count("merchant_verified") == 2 and "listing_claimed" in events