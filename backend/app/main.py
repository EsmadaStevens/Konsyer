"""API entry point. Run from backend/:  uvicorn app.main:app --reload --port 8000
Set KONSYER_STUB=1 to return a canned reply without calling the model."""
import os
import uuid
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import config  # noqa: F401  (loads .env)
from . import executor, wallet
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


class ActionIn(BaseModel):
    user_id: int = 1


class TopupIn(BaseModel):
    user_id: int = 1
    amount_ngwee: int
    idempotency_key: Optional[str] = None


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


# ---------- wallet (sandbox money, not real funds) ----------
@app.get("/wallet")
def get_wallet(user_id: int = 1, conn=Depends(db)):
    p = conn.execute("SELECT autonomy, per_purchase_cap, daily_cap FROM profiles WHERE user_id=?",
                     (user_id,)).fetchone()
    if p is None:
        raise HTTPException(status_code=404, detail="user not found")
    txns = conn.execute("SELECT amount, kind, ref, created_at FROM wallet_txns WHERE user_id=? "
                        "ORDER BY id DESC LIMIT 10", (user_id,)).fetchall()
    return {"sandbox": True, "balance": wallet.balance(conn, user_id),
            "spent_today": wallet.spent_today(conn, user_id, lusaka_today()),
            "autonomy": p["autonomy"], "per_purchase_cap": p["per_purchase_cap"], "daily_cap": p["daily_cap"],
            "recent": [dict(t) for t in txns]}


@app.post("/wallet/topup")
def topup(body: TopupIn, conn=Depends(db)):
    key = body.idempotency_key or f"topup-{uuid.uuid4()}"
    try:
        r = wallet.topup(conn, body.user_id, body.amount_ngwee, key)
        conn.commit()
    except wallet.WalletError as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    return {"sandbox": True, **r}


# ---------- approvals ----------
@app.get("/actions/pending")
def pending(user_id: int = 1, conn=Depends(db)):
    return {"actions": executor.list_pending(conn, user_id)}


def _with_card(conn, user_id, result):
    row = conn.execute("SELECT plan_id FROM pending_actions WHERE id=? AND user_id=?",
                       (result["action_id"], user_id)).fetchone()
    result["card"] = plan_card(conn, row["plan_id"]) if row and row["plan_id"] else None
    return result


def _run(conn, user_id, fn, action_id):
    try:
        return _with_card(conn, user_id, fn(conn, user_id, action_id))
    except executor.ExecError as e:
        raise HTTPException(status_code=404 if e.code == "not_found" else 409, detail=e.message)


@app.post("/actions/{action_id}/approve")
def approve_action(action_id: int, body: ActionIn, conn=Depends(db)):
    return _run(conn, body.user_id, executor.approve, action_id)


@app.post("/actions/{action_id}/deny")
def deny_action(action_id: int, body: ActionIn, conn=Depends(db)):
    return _run(conn, body.user_id, executor.deny, action_id)


@app.get("/plans/{plan_id}")
def get_plan(plan_id: int, user_id: int = 1, conn=Depends(db)):
    owner = conn.execute("SELECT user_id FROM plans WHERE id=?", (plan_id,)).fetchone()
    if owner is None or owner["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="plan not found")
    return {"card": plan_card(conn, plan_id)}