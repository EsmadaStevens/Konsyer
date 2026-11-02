import pytest
from fastapi.testclient import TestClient

from app import main, merchants
from app.agent.cards import plan_card
from app.db import get_conn, init_db
from app.merchants import MerchantError
from app.seed import seed
from app.timeutil import lusaka_today

TODAY = lusaka_today()
JPG = b"\xff\xd8\xff" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def conn():
    c = get_conn(":memory:")
    init_db(c)
    seed(c, TODAY)
    c.execute("INSERT INTO users(id, name) VALUES (2, 'Owner')")
    c.execute("INSERT INTO users(id, name) VALUES (3, 'Other')")
    c.execute("INSERT INTO merchants(id, name, owner_user_id, verified) VALUES (9, 'Shop', 2, 1)")
    c.execute("INSERT INTO listings(id, merchant_id, type, name, description, price, status, source) "
              "VALUES (90, 9, 'service', 'Braids', 'x', 1000, 'verified', 'merchant')")
    c.execute("INSERT INTO listings(id, merchant_id, type, name, description, price, status, source) "
              "VALUES (91, 9, 'service', 'New one', 'x', 1000, 'claimed', 'merchant')")
    c.commit()
    yield c
    c.close()


def test_a_verified_owner_can_attach_a_picture_and_customers_see_it_once_the_listing_is_verified(conn):
    merchants.set_photo(conn, 2, 90, JPG)
    data, ext = merchants.get_photo(conn, 90)  # no user, not admin: public
    assert data == JPG and ext == ".jpg"


def test_an_unverified_business_cannot_add_pictures(conn):
    conn.execute("UPDATE merchants SET verified=0 WHERE id=9")
    conn.commit()
    with pytest.raises(MerchantError) as e:
        merchants.set_photo(conn, 2, 90, JPG)
    assert e.value.code == "forbidden"


def test_only_the_owner_can_change_a_picture(conn):
    with pytest.raises(MerchantError):
        merchants.set_photo(conn, 3, 90, JPG)
    merchants.set_photo(conn, 2, 90, JPG)
    with pytest.raises(MerchantError):
        merchants.remove_photo(conn, 3, 90)


@pytest.mark.parametrize("bad", [b"", b"not an image at all", b"\xff\xd8\xff" + b"\x00" * (3 * 1024 * 1024)])
def test_only_small_real_images_are_accepted(conn, bad):
    with pytest.raises(MerchantError) as e:
        merchants.set_photo(conn, 2, 90, bad)
    assert e.value.code == "invalid"


def test_a_picture_on_a_listing_that_is_not_verified_yet_is_private(conn):
    merchants.set_photo(conn, 2, 91, PNG)
    with pytest.raises(MerchantError):
        merchants.get_photo(conn, 91)                      # public: no
    with pytest.raises(MerchantError):
        merchants.get_photo(conn, 91, user_id=3)           # another user: no
    assert merchants.get_photo(conn, 91, user_id=2)[1] == ".png"   # the owner: yes
    assert merchants.get_photo(conn, 91, admin=True)[1] == ".png"  # the admin before verifying: yes


def test_removing_a_picture(conn):
    merchants.set_photo(conn, 2, 90, JPG)
    merchants.remove_photo(conn, 2, 90)
    with pytest.raises(MerchantError):
        merchants.get_photo(conn, 90)


def test_the_owner_and_admin_lists_say_whether_a_listing_has_a_picture(conn):
    merchants.set_photo(conn, 2, 91, JPG)
    rows = {l["id"]: l["has_photo"] for l in merchants.get_merchant(conn, 2, 9)["listings"]}
    assert rows == {90: 0, 91: 1} or rows == {90: False, 91: True}
    pend = merchants.admin_list(conn)[0]["pending_listings"]
    assert pend[0]["listing_id"] == 91 and pend[0]["has_photo"] is True


def test_the_plan_card_points_at_the_picture_only_for_a_verified_listing(conn):
    merchants.set_photo(conn, 2, 90, JPG)
    merchants.set_photo(conn, 2, 91, JPG)
    conn.execute("INSERT INTO plans(id, user_id, title, budget) VALUES (5, 1, 'P', 5000)")
    for lid in (90, 91):
        conn.execute("INSERT INTO pending_actions(plan_id, user_id, kind, payload, amount) VALUES (5,1,'book_listing',?,0)",
                     (f'{{"listing_id": {lid}, "label": "x{lid}", "time_utc": "2030-01-01T08:00:00Z"}}',))
    conn.commit()
    items = {i["label"]: i for i in plan_card(conn, 5)["items"]}
    assert items["x90"]["photo_url"] == "/listings/90/photo" and "photo_url" not in items["x91"]


def test_api_upload_and_view(tmp_path, monkeypatch):
    monkeypatch.setenv("KONSYER_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("KONSYER_ADMIN_KEY", "k")
    c = get_conn(str(tmp_path / "t.db"))
    init_db(c)
    seed(c, TODAY)
    c.execute("INSERT INTO users(id, name) VALUES (2, 'Owner')")
    c.execute("INSERT INTO merchants(id, name, owner_user_id, verified) VALUES (9, 'Shop', 2, 1)")
    c.execute("INSERT INTO listings(id, merchant_id, type, name, price, status, source) "
              "VALUES (91, 9, 'service', 'New', 1000, 'claimed', 'merchant')")
    c.commit()
    c.close()
    client = TestClient(main.app)
    r = client.post("/listings/91/photo", files={"image": ("a.jpg", JPG, "image/jpeg")}, data={"user_id": "2"})
    assert r.status_code == 200
    assert client.get("/listings/91/photo").status_code == 404
    assert client.get("/listings/91/photo?user_id=2").headers["content-type"] == "image/jpeg"
    assert client.get("/listings/91/photo", headers={"X-Admin-Key": "k"}).status_code == 200
    assert client.get("/listings/91/photo", headers={"X-Admin-Key": "wrong"}).status_code == 404
    bad = client.post("/listings/91/photo", files={"image": ("a.jpg", JPG, "image/jpeg")}, data={"user_id": "1"})
    assert bad.status_code == 404
