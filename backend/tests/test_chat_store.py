import json

import pytest
from fastapi.testclient import TestClient

from app import chat_store, main
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


def test_an_exchange_comes_back_in_order(conn):
    chat_store.save_exchange(conn, 1, "hello", "hi there", [], None, False)
    chat_store.save_exchange(conn, 1, "nails", "found some", [{"type": "contact", "name": "X"}], None, True)
    h = chat_store.history(conn, 1)
    assert [m["text"] for m in h] == ["hello", "hi there", "nails", "found some"]
    assert [m["from_user"] for m in h] == [True, False, True, False]
    assert h[3]["cards"] == [{"type": "contact", "name": "X"}]


def test_only_the_newest_message_keeps_the_web_chip(conn):
    chat_store.save_exchange(conn, 1, "a", "b", [], None, True)
    chat_store.save_exchange(conn, 1, "c", "d", [], None, True)
    chips = [m["suggest_web"] for m in chat_store.history(conn, 1)]
    assert chips == [False, False, False, True]


def test_a_plan_card_is_rebuilt_not_replayed(conn):
    conn.execute("INSERT INTO plans(id, user_id, title, budget) VALUES (5, 1, 'P', 5000)")
    conn.execute("INSERT INTO pending_actions(plan_id, user_id, kind, payload, amount, status) VALUES (5,1,'book_listing',?,0,'pending')",
                 ('{"listing_id": 1, "label": "thing", "time_utc": "2030-01-01T08:00:00Z"}',))
    conn.commit()
    stale = {"type": "plan", "title": "OLD", "items": [{"status": "pending"}]}
    chat_store.save_exchange(conn, 1, "plan it", "done", [stale], 5, False)
    assert "OLD" not in json.dumps(chat_store.history(conn, 1))      # the stored snapshot was dropped
    conn.execute("UPDATE pending_actions SET status='executed' WHERE plan_id=5")
    conn.commit()
    card = chat_store.history(conn, 1)[1]["cards"][0]
    assert card["type"] == "plan" and card["items"][0]["status"] == "executed"   # fresh state


def test_history_is_per_user_and_capped_and_can_be_cleared(conn):
    conn.execute("INSERT INTO users(id, name) VALUES (2, 'B')")
    for i in range(40):
        chat_store.save_exchange(conn, 1, f"q{i}", f"a{i}", [], None, False)
    chat_store.save_exchange(conn, 2, "mine", "yours", [], None, False)
    assert len(chat_store.history(conn, 1)) == chat_store.KEEP
    assert chat_store.history(conn, 1)[-1]["text"] == "a39"
    chat_store.clear(conn, 1)
    assert chat_store.history(conn, 1) == [] and len(chat_store.history(conn, 2)) == 2


def test_api_saves_what_the_agent_says_and_returns_it(tmp_path, monkeypatch):
    monkeypatch.setenv("KONSYER_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("LLM_API_KEY", "dummy")
    monkeypatch.delenv("KONSYER_STUB", raising=False)
    c = get_conn(str(tmp_path / "t.db"))
    init_db(c)
    seed(c, TODAY)
    c.close()
    monkeypatch.setattr(main, "run_agent", lambda conn, uid, msg, hist: {"reply": "ok", "plan_id": None, "contacts": [], "trace": []})
    client = TestClient(main.app)
    client.post("/chat", json={"message": "hi", "user_id": 1, "history": []})
    got = client.get("/chat/history?user_id=1").json()["messages"]
    assert [m["text"] for m in got] == ["hi", "ok"]
    client.post("/chat/clear", json={"user_id": 1})
    assert client.get("/chat/history?user_id=1").json()["messages"] == []
