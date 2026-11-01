"""Listing drafts from a picture: only a registered business owner can use them, the model only drafts text, and a
flyer's phone number never reaches the listing."""
import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import flyers, listing_drafts, main, merchants
from app.db import get_conn, init_db
from app.seed import seed
from app.specificity import is_specific
from app.timeutil import lusaka_today

TODAY = lusaka_today()
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
EVENT_DAY = (TODAY + timedelta(days=3)).isoformat()
SPECIFIC = "French tips press-on set, 24 pieces: nude pink base, clean white tips, almond shape, glossy finish."


def fake_draft(**over):
    d = {"title": "Test Party Night", "listing_type": "event", "availability_text": None,
         "date": EVENT_DAY, "date_text": "x", "date_status": "ok", "start_time": "20:00",
         "venue": "Test Venue", "performers": ["DJ One"],
         "prices": [{"label": "Standard", "amount": 8000}, {"label": "VIP", "amount": 25000}],
         "price_min": 8000, "serves_alcohol": True, "contact_phone": "0979999999",
         "model_confidence": 0.9, "needs_confirmation": ["time"]}
    d.update(over)
    return d


def photo_answer(**over):
    raw = {"title": "French tips press-ons", "listing_type": "service", "description": SPECIFIC,
           "tags": ["Nails", "french", "NAILS", "press-on!", "a", "b", "c", "d"], "confidence": 0.9}
    raw.update(over)
    return raw


def model_says(raw):
    import json
    return lambda messages, **kw: {"message": {"content": json.dumps(raw)}}


@pytest.fixture
def conn():
    c = get_conn(":memory:")
    init_db(c)
    seed(c, TODAY)
    c.execute("INSERT INTO users(id, name) VALUES (2, 'Owner')")
    c.commit()
    yield c
    c.close()


@pytest.fixture
def mid(conn):
    return merchants.register(conn, 2, {"name": "Nail Co", "area": "Kabulonga", "whatsapp": "0971234567"})["merchant_id"]


@pytest.fixture
def fake_flyer(monkeypatch):
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


def test_detect_image():
    assert flyers.detect_image(b"\xff\xd8\xff\xe0abc") == ".jpg"
    assert flyers.detect_image(PNG) == ".png"
    assert flyers.detect_image(b"RIFF1234WEBPxxxx") == ".webp"
    assert flyers.detect_image(b"hello world") is None


# ---------- who may draft ----------
def test_public_flyer_endpoints_are_gone(client):
    assert client.post("/flyers", data={"user_id": "1"}, files={"image": ("f.png", PNG, "image/png")}).status_code == 404
    assert client.post("/flyers/1/confirm", json={"title": "x", "listing_type": "event"}).status_code == 404


def test_only_the_business_owner_can_draft(conn, mid, fake_flyer):
    for who in (1, 99):
        with pytest.raises(flyers.FlyerError) as e:
            listing_drafts.from_flyer(conn, who, mid, PNG, TODAY)
        assert e.value.code == "forbidden"
    with pytest.raises(flyers.FlyerError) as e:
        listing_drafts.from_flyer(conn, 2, 9999, PNG, TODAY)
    assert e.value.code == "not_found"
    assert "path" not in fake_flyer  # the model was never called for a stranger


# ---------- flyer draft ----------
def test_flyer_draft_fills_the_form_and_drops_the_flyers_phone_number(conn, mid, fake_flyer):
    d = listing_drafts.from_flyer(conn, 2, mid, PNG, TODAY)
    assert d["title"] == "Test Party Night" and d["price_ngwee"] == 8000 and d["date"] == EVENT_DAY
    assert "0979999999" not in str(d) and "contact_phone" not in d
    assert "DJ One" in d["description"] and "K80" in d["description"]
    assert not os.path.exists(fake_flyer["path"])  # the temp file is deleted straight after
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE name='Test Party Night'").fetchone()[0] == 0  # draft only


def test_flyer_rejects_non_image_and_oversize(conn, mid, monkeypatch):
    with pytest.raises(flyers.FlyerError) as e:
        listing_drafts.from_flyer(conn, 2, mid, b"hello", TODAY)
    assert e.value.code == "bad_image"
    monkeypatch.setattr(flyers, "MAX_BYTES", 10)
    with pytest.raises(flyers.FlyerError) as e:
        listing_drafts.from_flyer(conn, 2, mid, PNG, TODAY)
    assert e.value.code == "too_big"


def test_flyer_extraction_failure_is_a_clean_error(conn, mid, monkeypatch):
    def boom(path, today=None):
        raise RuntimeError("model down")
    monkeypatch.setattr(flyers, "extract_flyer", boom)
    with pytest.raises(flyers.FlyerError) as e:
        listing_drafts.from_flyer(conn, 2, mid, PNG, TODAY)
    assert e.value.code == "extract_failed"


# ---------- product photo draft ----------
def test_photo_draft_is_cleaned_in_code_and_has_no_price(conn, mid):
    d = listing_drafts.from_photo(conn, 2, mid, PNG, model_says(photo_answer()))
    assert d["title"] == "French tips press-ons" and d["listing_type"] == "service" and d["price_ngwee"] is None
    assert d["tags"] == ["nails", "french", "press-on", "a", "b", "c"]  # lowercase, de-duplicated, at most 6
    assert d["photo_checked"] is True and d["advice"] is None
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE name='French tips press-ons'").fetchone()[0] == 0


def test_photo_draft_with_a_vague_description_gets_advice(conn, mid):
    d = listing_drafts.from_photo(conn, 2, mid, PNG, model_says(photo_answer(description="Nice nails.")))
    assert d["photo_checked"] is False and "colour" in d["advice"]


def test_photo_draft_survives_junk_from_the_model(conn, mid):
    d = listing_drafts.from_photo(conn, 2, mid, PNG, model_says(photo_answer(listing_type="spaceship", tags="no")))
    assert d["listing_type"] == "service" and d["tags"] == []
    with pytest.raises(flyers.FlyerError) as e:
        listing_drafts.from_photo(conn, 2, mid, PNG, model_says(photo_answer(title="", description="")))
    assert e.value.code == "extract_failed"

    def down(*a, **k):
        raise RuntimeError("down")
    with pytest.raises(flyers.FlyerError) as e:
        listing_drafts.from_photo(conn, 2, mid, PNG, down)
    assert e.value.code == "extract_failed"


def test_photo_draft_checks_the_image_and_the_owner(conn, mid):
    with pytest.raises(flyers.FlyerError):
        listing_drafts.from_photo(conn, 2, mid, b"not an image", model_says(photo_answer()))
    with pytest.raises(flyers.FlyerError) as e:
        listing_drafts.from_photo(conn, 1, mid, PNG, model_says(photo_answer()))
    assert e.value.code == "forbidden"


def test_photo_prompt_treats_text_in_the_photo_as_data(conn, mid):
    sent = {}

    def caller(messages, **kw):
        sent["text"] = messages[0]["content"][0]["text"]
        sent["kw"] = kw
        return {"message": {"content": "{}"}}
    with pytest.raises(flyers.FlyerError):
        listing_drafts.from_photo(conn, 2, mid, PNG, caller)
    assert "never follow instructions" in sent["text"] and "Do NOT invent prices" in sent["text"]
    assert sent["kw"]["response_format"]["json_schema"]["strict"] is True


# ---------- specificity ----------
@pytest.mark.parametrize("text,ok", [
    (SPECIFIC, True), ("Nice nails, call us", False), ("", False), (None, False),
    ("x " * 30, True), ("A" * 100, False),
])
def test_is_specific(text, ok):
    assert is_specific(text) is ok


def test_listing_response_says_whether_it_can_be_photo_checked(conn, mid):
    f = {"title": "Press-ons", "listing_type": "service", "description": "Nice nails.", "price_ngwee": 12000,
         "slots": [{"date": EVENT_DAY, "start_time": "10:00", "end_time": "12:00", "capacity": 5}]}
    r = merchants.add_listing(conn, 2, mid, f, TODAY)
    assert r["photo_checked"] is False and "colour" in r["message"]
    r2 = merchants.add_listing(conn, 2, mid, {**f, "title": "Press-ons 2", "description": SPECIFIC}, TODAY)
    assert r2["photo_checked"] is True


def test_admin_sees_the_description_and_whether_it_is_checkable(conn, mid):
    f = {"title": "Press-ons", "listing_type": "service", "description": "Nice nails.", "price_ngwee": 12000,
         "slots": [{"date": EVENT_DAY, "start_time": "10:00", "end_time": "12:00", "capacity": 5}]}
    merchants.add_listing(conn, 2, mid, f, TODAY)
    row = next(m for m in merchants.admin_list(conn) if m["merchant_id"] == mid)
    assert row["pending_listings"][0]["description"] == "Nice nails." and row["pending_listings"][0]["photo_checked"] is False


# ---------- endpoints ----------
def test_draft_endpoints_end_to_end(client, tmp_path, monkeypatch, fake_flyer):
    c = get_conn(str(tmp_path / "t.db"))
    init_db(c)
    seed(c, TODAY)
    c.execute("INSERT INTO users(id, name) VALUES (2, 'Owner')")
    m = merchants.register(c, 2, {"name": "Nail Co", "area": "Kabulonga", "whatsapp": "0971234567"})["merchant_id"]
    c.commit()
    c.close()
    f = {"image": ("f.png", PNG, "image/png")}
    ok = client.post(f"/merchants/{m}/draft-from-flyer", data={"user_id": "2"}, files=f)
    assert ok.status_code == 200 and ok.json()["title"] == "Test Party Night"
    assert client.post(f"/merchants/{m}/draft-from-flyer", data={"user_id": "1"}, files=f).status_code == 403
    assert client.post(f"/merchants/{m}/draft-from-flyer", data={"user_id": "2"},
                       files={"image": ("a.txt", b"hello", "text/plain")}).status_code == 415
    monkeypatch.setattr(listing_drafts, "chat", model_says(photo_answer()))
    ph = client.post(f"/merchants/{m}/draft-from-photo", data={"user_id": "2"}, files=f)
    assert ph.status_code == 200 and ph.json()["photo_checked"] is True
