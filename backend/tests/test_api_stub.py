from fastapi.testclient import TestClient
from app import main


def client(monkeypatch, tmp_path):
    monkeypatch.setenv("KONSYER_DB", str(tmp_path / "t.db"))
    return TestClient(main.app)


def test_health(monkeypatch, tmp_path):
    assert client(monkeypatch, tmp_path).get("/health").json() == {"ok": True}


def test_chat_stub_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("KONSYER_STUB", "1")
    plan = client(monkeypatch, tmp_path).post("/chat", json={"message": "hi"}).json()["cards"][0]
    assert plan["type"] == "plan"
    assert plan["total"] == sum(i["amount"] for i in plan["items"]) <= plan["budget"]


def test_chat_uses_agent_and_returns_card(monkeypatch, tmp_path):
    monkeypatch.delenv("KONSYER_STUB", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "x")
    seen = {}

    def fake_agent(conn, user_id, message, history):
        seen["message"] = message
        return {"reply": "ok", "plan_id": None, "proposed": [], "trace": [], "steps": 1}

    monkeypatch.setattr(main, "run_agent", fake_agent)
    r = client(monkeypatch, tmp_path).post("/chat", json={"message": "hello"}).json()
    assert r["reply"] == "ok" and r["cards"] == [] and seen["message"] == "hello"