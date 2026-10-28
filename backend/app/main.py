"""API entry point. Run from backend/:  uvicorn app.main:app --reload --port 8000
Set KONSYER_STUB=1 to return a canned reply without calling the model."""
import os

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import config  # noqa: F401  (loads .env)
from .agent.cards import plan_card
from .agent.loop import run_agent
from .db import get_conn, init_db
from .llm import LLMError
from .seed import seed
from .timeutil import lusaka_today

app = FastAPI(title="Konsyer API")
# Wide open for the hackathon; tighten to the deployed web origin before going live.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def db():
    conn = get_conn(os.environ.get("KONSYER_DB", "konsyer.db"))
    try:
        init_db(conn)
        seed(conn, lusaka_today())
        yield conn
    finally:
        conn.close()


class ChatIn(BaseModel):
    user_id: int = 1
    message: str
    history: list[dict] = []


@app.get("/health")
def health():
    return {"ok": True}


def _stub(message):
    return {
        "stub": True,
        "reply": f"(stub) You said: {message!r}. Here is an example plan using sample data.",
        "cards": [{"type": "plan", "title": "Saturday plan for 5", "sample_data": True, "budget": 200000,
                   "items": [{"label": "Go-karting (sample listing)", "time": "15:00", "amount": 75000},
                             {"label": "Dinner (sample listing)", "time": "18:00", "amount": 90000},
                             {"label": "Ride home (simulated provider)", "time": "21:15", "amount": 25000}],
                   "total": 190000}],
    }


@app.post("/chat")
def chat(body: ChatIn, conn=Depends(db)):
    if os.environ.get("KONSYER_STUB") == "1" or not os.environ.get("LLM_API_KEY"):
        return _stub(body.message)
    try:
        r = run_agent(conn, body.user_id, body.message, body.history)
    except (LLMError, httpx.HTTPError) as e:
        raise HTTPException(status_code=502, detail=f"model unavailable: {e}")
    card = plan_card(conn, r["plan_id"]) if r["plan_id"] else None
    return {"stub": False, "reply": r["reply"], "cards": [card] if card else [],
            "trace": [{"tool": t["tool"], "args": t["args"]} for t in r["trace"]]}