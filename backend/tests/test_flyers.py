import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import executor, flyers, main
from app.agent.tools import Tools
from app.db import get_conn, init_db
from app.seed import seed
from app.timeutil import lusaka_today

TODAY = lusaka_today()
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
EVENT_DAY = (TODAY + timedelta(days=3)).isoformat()


def fake_draft(**over):
    d = {"title": "Test Party Night", "listing_type": "event", "availability_text": None,
         "date": EVENT_DAY, "date_text": "x", "date_status": "ok", "start_time": "20:00",
         "venue": "Test Venue", "performers": [],
         "prices": [{"label": "Standard", "amount": 8000}, {"label": "VIP", "amount": 25000}],
         "price_min": 8000, "serves_alcohol": False, "contact_phone": "0971234567",
         "model_confidence": 0.9, "needs_confirmation": []}
    d.update(over)
    return d


def fields(**over):
    f = {"title": "Test Party Night", "listing_type": "event", "date": EVENT_DAY, "start_time": "20:00",
         "venue": "Test Venue", "area": "Test Area", "price_ngwee": 8000, "serves_alcohol": False,
         "contact_phone": "0971234567", "performers": [], "availability_text": None}
    f.update(over)
    return f


@pytest.fixture
def conn():
    c = get_conn(":memory:")
    init_db(c)
    seed(c, TODAY)
    yield c
    c.close()


@pytest.fixture
def fake_model(monkeypatch):
    seen = {}

    def fake(path, today=None):
        seen["path"] = path
        assert os.path.exists(path)
        return fake_draft()

    monkeypatch.setattr(flyers, "extract_flyer", fake)
    return seen


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KONSYER_DB", str(tmp_path / "t.db"))
    return TestClient(main.app)


def new_draft(conn):
    return flyers.create_draft(conn, 1, "flyer", PNG, TODAY)["flyer_id"]


def count_flyer_listings(conn):
    return conn.execute("SELECT COUNT(*) FROM listings WHERE source_url LIKE 'flyer:%'").fetchone()[0]

def test_detect_image():
    assert flyers.detect_image(b"\xff\xd8\xff\xe0abc") == ".jpg"
    assert flyers.detect_image(PNG) == ".png"
    assert flyers.detect_image(b"RIFF1234WEBPxxxx") == ".webp"
    assert flyers.detect_image(b"hello world") is None


def test_upload_returns_draft_and_deletes_temp_file(client, fake_model):
    r = client.post("/flyers", data={"user_id": "1", "kind": "flyer"},
                    files={"image": ("f.png", PNG, "image/png")})
    assert r.status_code == 200
    j = r.json()
    assert j["draft"]["title"] == "Test Party Night" and j["flyer_id"]
    assert not os.path.exists(fake_model["path"])


def test_upload_rejects_non_image(client):
    r = client.post("/flyers", data={"user_id": "1", "kind": "flyer"},
                    files={"image": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_upload_rejects_too_big(client, monkeypatch):
    monkeypatch.setattr(flyers, "MAX_BYTES", 10)
    r = client.post("/flyers", data={"user_id": "1", "kind": "flyer"},
                    files={"image": ("f.png", PNG, "image/png")})
    assert r.status_code == 413


def test_extraction_failure_is_a_clean_error(conn, monkeypatch):
    def boom(path, today=None):
        raise RuntimeError("model down")

    monkeypatch.setattr(flyers, "extract_flyer", boom)
    with pytest.raises(flyers.FlyerError) as e:
        flyers.create_draft(conn, 1, "flyer", PNG, TODAY)
    assert e.value.code == "extract_failed"
    assert conn.execute("SELECT COUNT(*) FROM flyers").fetchone()[0] == 0


def test_confirm_creates_unverified_listing_and_slot(conn, fake_model):
    fid = new_draft(conn)
    r = flyers.confirm(conn, 1, fid, fields(), TODAY)
    l = conn.execute("SELECT * FROM listings WHERE id=?", (r["listing_id"],)).fetchone()
    assert (l["source"], l["status"], l["trust_score"], l["is_sample"]) == ("flyer", "unverified", 0.3, 0)
    assert l["contact_whatsapp"] == "260971234567"
    assert "Lineup not listed" in l["description"]
    s = conn.execute("SELECT * FROM slots WHERE listing_id=?", (l["id"],)).fetchone()
    assert s["starts_at"].endswith("T18:00:00Z")  # 20:00 Lusaka = 18:00 UTC
    assert conn.execute("SELECT status FROM flyers WHERE id=?", (fid,)).fetchone()["status"] == "confirmed"


def test_flyer_listing_always_needs_approval_even_with_auto_buy(conn, fake_model):
    fid = new_draft(conn)
    r = flyers.confirm(conn, 1, fid, fields(), TODAY)
    conn.execute("UPDATE profiles SET autonomy='auto_small', auto_buy_under=1000000 WHERE user_id=1")
    conn.commit()
    tools = Tools(conn, 1, TODAY)
    found = tools.search_listings(query="test party", date=EVENT_DAY)
    assert r["listing_id"] in [x["listing_id"] for x in found["listings"]]
    out = tools.propose_action("book_listing", r["listing_id"], r["slot_id"], 2, plan_title="Test")
    assert out["decision"] == "CONFIRM"
    assert conn.execute("SELECT policy_rule FROM pending_actions WHERE id=?",
                        (out["action_id"],)).fetchone()["policy_rule"] == "unverified_payee"
    done = executor.approve(conn, 1, out["action_id"], TODAY)
    assert done["ok"] and done["outcome"] == "executed"


def test_confirm_is_idempotent(conn, fake_model):
    fid = new_draft(conn)
    a = flyers.confirm(conn, 1, fid, fields(), TODAY)
    b = flyers.confirm(conn, 1, fid, fields(), TODAY)
    assert b["already_confirmed"] and b["listing_id"] == a["listing_id"]
    assert count_flyer_listings(conn) == 1


@pytest.mark.parametrize("over", [
    {"title": "  "}, {"listing_type": "party"}, {"date": "2000-01-01"}, {"date": "31/10/2026"},
    {"start_time": "25:00"}, {"price_ngwee": 6_000_000}, {"price_ngwee": 0}, {"contact_phone": "12"},
])
def test_confirm_rejects_bad_fields(conn, fake_model, over):
    fid = new_draft(conn)
    with pytest.raises(flyers.FlyerError) as e:
        flyers.confirm(conn, 1, fid, fields(**over), TODAY)
    assert e.value.code == "invalid"
    assert count_flyer_listings(conn) == 0


def test_other_user_cannot_confirm(conn, fake_model):
    fid = new_draft(conn)
    with pytest.raises(flyers.FlyerError) as e:
        flyers.confirm(conn, 2, fid, fields(), TODAY)
    assert e.value.code == "not_found"


def test_service_listing_has_no_slot_and_lineup_is_not_invented(conn, fake_model):
    fid = new_draft(conn)
    r = flyers.confirm(conn, 1, fid, fields(listing_type="service", date=None, start_time=None,
                                            contact_phone="+260 97 123 4567", availability_text="Mon - Thu"),
                       TODAY)
    assert r["slot_id"] is None
    l = conn.execute("SELECT * FROM listings WHERE id=?", (r["listing_id"],)).fetchone()
    assert l["contact_whatsapp"] == "260971234567" and l["starts_at"] is None
    assert "Lineup" not in l["description"] and "Mon - Thu" in l["description"]


def test_confirm_endpoint_end_to_end(client, fake_model):
    up = client.post("/flyers", data={"user_id": "1", "kind": "screenshot"},
                     files={"image": ("s.png", PNG, "image/png")}).json()
    body = {"user_id": 1, "title": "Test Party Night", "listing_type": "event", "date": EVENT_DAY,
            "start_time": "20:00", "venue": "Test Venue", "price_ngwee": 8000}
    r = client.post(f"/flyers/{up['flyer_id']}/confirm", json=body)
    assert r.status_code == 200 and r.json()["status"] == "unverified"
    bad = client.post(f"/flyers/{up['flyer_id'] + 99}/confirm", json=body)
    assert bad.status_code == 404