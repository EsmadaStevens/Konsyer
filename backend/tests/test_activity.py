"""The Activity view: model calls are metadata only, and the audit trail says who acted."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import activity, llm, main
from app.db import get_conn, init_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KONSYER_DB", str(tmp_path / "t.db"))
    return TestClient(main.app)


def test_record_stores_metadata_only(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    monkeypatch.setenv("KONSYER_DB", str(db))
    activity.record("dispute_vision", "muse-spark-open", True, 1234, "")
    conn = get_conn(str(db))
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(model_calls)")]
    assert cols == ["id", "job", "model", "ok", "ms", "note", "created_at"]  # nothing that could hold a prompt
    row = conn.execute("SELECT * FROM model_calls").fetchone()
    assert (row["job"], row["model"], row["ok"], row["ms"]) == ("dispute_vision", "muse-spark-open", 1, 1234)


def test_unknown_job_is_filed_under_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("KONSYER_DB", str(tmp_path / "a.db"))
    activity.record("surprise", "m", False, -5, "x" * 500)
    row = get_conn(str(tmp_path / "a.db")).execute("SELECT * FROM model_calls").fetchone()
    assert row["job"] == "agent" and row["ms"] == 0 and row["ok"] == 0


def test_observer_errors_never_reach_the_caller(monkeypatch):
    def boom(*a):
        raise RuntimeError("db down")
    monkeypatch.setattr(llm, "_observer", boom)
    llm.observe("agent", "m", True, 5)  # must not raise


def test_chat_reports_job_and_outcome(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "_observer", lambda *a: seen.append(a))
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LLM_MODEL", "muse-test")

    class R:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "hi"}}]}

    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: R())
    with llm.as_job("flyer_vision"):
        llm.chat([{"role": "user", "content": "hello"}])
    llm.chat([{"role": "user", "content": "hello"}])
    assert [(s[0], s[2]) for s in seen] == [("flyer_vision", True), ("agent", True)]
    assert all("hello" not in str(s) for s in seen)  # the prompt is never passed on


def test_failed_call_is_recorded_as_failed(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "_observer", lambda *a: seen.append(a))
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "http://x")

    class R:
        status_code = 500
        text = "secret body"
        def json(self):
            return {}

    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: R())
    with pytest.raises(llm.LLMError):
        llm.chat([{"role": "user", "content": "x"}])
    assert seen[0][2] is False and seen[0][4] == "LLMError" and "secret" not in str(seen)


@pytest.mark.parametrize("event,rule,actor", [
    ("policy_deny", "per_purchase_cap", "rule_blocked"),
    ("hold_blocked", "x", "rule_blocked"),
    ("approved_by_user", None, "person"),
    ("dispute_refunded", "human_decides", "person"),
    ("dispute_assessed", "dispute_vision_recommendation_only", "model"),
    ("contact_drafted", None, "model"),
    ("voucher_held", "escrow", "rule"),
])
def test_actor_is_named_for_each_kind_of_step(event, rule, actor):
    assert activity._outcome(rule, event) == actor


def test_activity_endpoint_shape(client):
    activity.record("web_search", "muse-spark-1.3", True, 9000)
    activity.record("agent", "muse-spark-open", True, 800)
    activity.record("agent", "muse-spark-open", False, 100, "LLMError")
    j = client.get("/activity?user_id=1").json()
    assert [c["job"] for c in j["model_calls"]] == ["agent", "agent", "web_search"]  # newest first
    assert j["model_calls"][0]["ok"] is False and "what" in j["model_calls"][0]
    agent = next(t for t in j["model_totals"] if t["job"] == "agent")
    assert agent["calls"] == 2 and agent["worked"] == 1
    assert "no prompts" in j["note"]
    assert isinstance(j["audit"], list)


def test_limit_is_clamped(client):
    assert client.get("/activity?limit=100000").status_code == 200
    assert client.get("/activity?limit=0").status_code == 200
