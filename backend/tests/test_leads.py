from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import leads, main
from app.agent.tools import Tools
from app.db import get_conn, init_db
from app.leads import LeadError
from app.seed import seed
from app.timeutil import lusaka_today

TODAY = lusaka_today()


@pytest.fixture
def conn():
    c = get_conn(":memory:")
    init_db(c)
    seed(c, TODAY)
    for uid in (2, 3):
        c.execute("INSERT INTO users(id, name) VALUES (?, 'u')", (uid,))
    c.commit()
    yield c
    c.close()


def add_lead(conn, name="Glam Nails", phone="0977123456", price="K250 for masai twist", price_for="masai twist"):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return conn.execute(
        "INSERT INTO web_leads(search_key, name, area, phone, price_text, price_for, source_url, platform, "
        "corroborated, is_sample, created_at) VALUES ('k', ?, 'Kabulonga', ?, ?, ?, 'https://example.com/p', "
        "'website', 0, 0, ?)", (name, phone, price, price_for, stamp)).lastrowid


def test_short_price_keeps_the_amount_and_trims_a_long_post():
    assert leads.short_price("K250 for masai twist") == "K250 for masai twist"
    long = "Hello beautiful people! " * 20 + "Box braids from K350 this weekend only. " + "Call us now! " * 20
    out = leads.short_price(long)
    assert "K350" in out and len(out) < 140 and out.startswith("...") and out.endswith("...")
    assert leads.short_price(None) == ""


def test_card_shows_the_price_hint_exactly_and_ignores_unknown_ids(conn):
    a = add_lead(conn)
    b = add_lead(conn, name="No Price", phone="0966111222", price=None, price_for=None)
    card = leads.card(conn, 1, [a, b, 9999])
    assert card["type"] == "leads" and [x["name"] for x in card["leads"]] == ["Glam Nails", "No Price"]
    assert card["leads"][0]["price_text"] == "K250 for masai twist" and card["leads"][0]["price_for"] == "masai twist"
    assert card["leads"][1]["price_text"] is None
    assert leads.card(conn, 1, [9999]) is None


def test_phone_can_be_hidden(conn, monkeypatch):
    monkeypatch.setenv("DISCOVERY_SHOW_PHONES", "0")
    v = leads.card(conn, 1, [add_lead(conn)])["leads"][0]
    assert v["phone"] is None and v["has_whatsapp"] is False


def test_ids_from_trace_only_reads_discover_results():
    trace = [{"tool": "search_listings", "result": {"leads": [{"lead_id": "L9"}]}},
             {"tool": "discover_services", "result": {"leads": [{"lead_id": "L3"}, {"lead_id": "L4"}, {"lead_id": "L3"},
                                                                  {"lead_id": "evil"}]}},
             {"tool": "discover_services", "result": {"error": "x"}}]
    assert leads.ids_from_trace(trace) == [3, 4]


def test_draft_contact_is_built_by_code_and_strips_links(conn):
    lid = add_lead(conn)
    c = leads.draft_contact(conn, 1, lid, "braids Saturday, see http://evil.example now")
    assert "evil.example" not in c["message"] and "braids Saturday" in c["message"]
    assert [x["kind"] for x in c["channels"]][:1] == ["whatsapp"] and c["verified"] is False
    with pytest.raises(LeadError) as e:
        leads.draft_contact(conn, 1, 9999, "x")
    assert e.value.code == "not_found"


def test_feedback_validates_and_hides_for_the_reporter_only(conn):
    lid = add_lead(conn)
    with pytest.raises(LeadError):
        leads.record_feedback(conn, 1, lid)
    with pytest.raises(LeadError):
        leads.record_feedback(conn, 1, lid, status="bogus")
    with pytest.raises(LeadError):
        leads.record_feedback(conn, 1, lid, price="cheap")
    r = leads.record_feedback(conn, 1, lid, status="closed")
    assert r["hidden"] is True and r["signals"]["closed"] == 1
    row = conn.execute("SELECT * FROM web_leads WHERE id=?", (lid,)).fetchall()
    assert leads.visible(conn, 1, row) == [] and len(leads.visible(conn, 2, row)) == 1


def test_one_report_per_user_per_lead_can_be_changed_and_keeps_the_price_answer(conn):
    lid = add_lead(conn)
    leads.record_feedback(conn, 1, lid, price="differs")
    leads.record_feedback(conn, 1, lid, status="open")
    r = leads.record_feedback(conn, 1, lid, status="no_reply")
    assert r["signals"]["no_reply"] == 1 and r["signals"]["open"] == 0 and r["signals"]["price_differs"] == 1
    assert conn.execute("SELECT COUNT(*) FROM lead_feedback").fetchone()[0] == 1


def test_lead_disappears_for_everyone_only_after_two_closed_reports_outweigh_open(conn):
    lid = add_lead(conn)
    row = lambda: conn.execute("SELECT * FROM web_leads WHERE id=?", (lid,)).fetchall()
    leads.record_feedback(conn, 2, lid, status="closed")
    assert len(leads.visible(conn, 3, row())) == 1  # one report is not enough
    leads.record_feedback(conn, 3, lid, status="open")
    leads.record_feedback(conn, 1, lid, price="matched")
    assert len(leads.visible(conn, 1, row())) == 1  # 1 closed vs 1 open
    conn.execute("UPDATE lead_feedback SET status='closed' WHERE user_id=3")
    conn.commit()
    assert leads.visible(conn, 1, row()) == []  # 2 closed, 0 open


def test_same_business_under_a_new_lead_row_keeps_its_reports(conn):
    first = add_lead(conn)
    leads.record_feedback(conn, 1, first, status="open", price="matched")
    second = add_lead(conn)  # a later search stored the same number again
    v = leads.card(conn, 2, [second])["leads"][0]
    assert v["signals"]["open"] == 1 and v["signals"]["price_matched"] == 1


def test_agent_search_skips_a_lead_the_user_reported_closed(conn):
    t = Tools(conn, 1, TODAY)
    out = t.discover_services("nails", "Kabulonga")
    assert out["count"] >= 1
    first = int(out["leads"][0]["lead_id"][1:])
    leads.record_feedback(conn, 1, first, status="closed")
    again = Tools(conn, 1, TODAY).discover_services("nails", "Kabulonga")
    assert f"L{first}" not in [x["lead_id"] for x in again["leads"]]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KONSYER_DB", str(tmp_path / "t.db"))
    return TestClient(main.app)


def test_api_contact_and_feedback(client, tmp_path):
    c = get_conn(str(tmp_path / "t.db"))
    init_db(c)
    seed(c, TODAY)
    lid = add_lead(c)
    c.commit()
    c.close()
    r = client.post(f"/leads/{lid}/contact", json={"details": "french tips Saturday"}).json()
    assert r["type"] == "contact" and "french tips" in r["message"] and r["invite_message"]
    f = client.post(f"/leads/{lid}/feedback", json={"status": "open", "price": "differs"}).json()
    assert f["ok"] and f["signals"]["open"] == 1 and f["signals"]["price_differs"] == 1
    assert client.post(f"/leads/{lid}/feedback", json={}).status_code == 400
    assert client.post("/leads/999/contact", json={"details": "x"}).status_code == 404
    assert client.post("/leads/999/feedback", json={"status": "open"}).status_code == 404


def test_mentioned_ids_finds_remembered_leads_named_in_the_reply(conn):
    from app import discovery
    a = add_lead(conn, name="Sarahs Hair Studio")
    b = add_lead(conn, name="Natural Kurls", phone="0966111222")
    discovery.remember(conn, 1, conn.execute("SELECT * FROM web_leads ORDER BY id").fetchall())
    assert leads.mentioned_ids(conn, 1, "I would try sarahs hair studio first") == [a]
    assert leads.mentioned_ids(conn, 1, "Natural Kurls and Sarahs Hair Studio") == [a, b]
    assert leads.mentioned_ids(conn, 1, "nothing relevant") == []
    assert leads.mentioned_ids(conn, 2, "Sarahs Hair Studio") == []  # another user was shown nothing


def test_card_skips_leads_the_user_reported_closed(conn):
    a = add_lead(conn)
    leads.record_feedback(conn, 1, a, status="closed")
    assert leads.card(conn, 1, [a]) is None
    assert leads.card(conn, 2, [a]) is not None


def test_chat_shows_the_card_when_the_model_answers_from_remembered_leads(client, tmp_path, monkeypatch):
    import json
    from app import discovery, llm
    c = get_conn(str(tmp_path / "t.db"))
    init_db(c)
    seed(c, TODAY)
    add_lead(c, name="Sarahs Hair Studio")
    discovery.remember(c, 1, c.execute("SELECT * FROM web_leads").fetchall())
    c.close()
    monkeypatch.setenv("LLM_API_KEY", "dummy")
    monkeypatch.setattr(llm, "chat", lambda *a, **k: {"message": {"content": "Try Sarahs Hair Studio.", "tool_calls": []}})
    r = client.post("/chat", json={"message": "hair in Kabulonga"}).json()
    assert [x["type"] for x in r["cards"]] == ["leads"] and r["cards"][0]["leads"][0]["name"] == "Sarahs Hair Studio"
    # a turn that drafts a contact does not repeat the lead card
    from app import main as m
    calls = {"n": 0}

    def fake(messages, tools=None, max_tokens=0, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            lid = c2_lead
            return {"message": {"content": "", "tool_calls": [{"id": "x", "type": "function", "function": {
                "name": "draft_contact_message", "arguments": json.dumps({"details": "braids", "lead_id": f"L{lid}"})}}]}}
        return {"message": {"content": "Drafted for Sarahs Hair Studio.", "tool_calls": []}}

    c2 = get_conn(str(tmp_path / "t.db"))
    c2_lead = c2.execute("SELECT id FROM web_leads").fetchone()["id"]
    c2.close()
    monkeypatch.setattr(llm, "chat", fake)
    r = client.post("/chat", json={"message": "message the first one"}).json()
    assert [x["type"] for x in r["cards"]] == ["contact"]