"""API entry point. Run from backend/:  uvicorn app.main:app --reload --port 8000
Set KONSYER_STUB=1 to return a canned reply without calling the model."""
import hmac
import os
import uuid
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import config  # noqa: F401  (loads .env)
from . import executor, flyers, merchants, wallet
from .agent.cards import contact_card, plan_card
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


class FlyerConfirmIn(BaseModel):
    user_id: int = 1
    title: str
    listing_type: str
    date: Optional[str] = None
    start_time: Optional[str] = None
    venue: Optional[str] = None
    area: Optional[str] = None
    price_ngwee: Optional[int] = None
    serves_alcohol: bool = False
    contact_phone: Optional[str] = None
    performers: list[str] = []
    availability_text: Optional[str] = None


class MerchantIn(BaseModel):
    user_id: int = 1
    name: str
    area: Optional[str] = None
    whatsapp: Optional[str] = None
    social_url: Optional[str] = None


class SlotIn(BaseModel):
    date: str
    start_time: str
    end_time: Optional[str] = None
    capacity: int = 1


class MerchantListingIn(BaseModel):
    user_id: int = 1
    title: str
    listing_type: str
    description: Optional[str] = None
    area: Optional[str] = None
    price_ngwee: Optional[int] = None
    serves_alcohol: bool = False
    tags: list[str] = []
    slots: list[SlotIn] = []


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
    cards = ([card] if card else []) + [contact_card(c) for c in r.get("contacts", [])]
    return {"stub": False, "reply": r["reply"], "cards": cards,
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


# ---------- flyer / screenshot ingest ----------
def _flyer_call(fn, *args):
    try:
        return fn(*args)
    except flyers.FlyerError as e:
        raise HTTPException(status_code=flyers.STATUS.get(e.code, 400), detail=e.message)


@app.post("/flyers")
def upload_flyer(image: UploadFile = File(...), user_id: int = Form(1), kind: str = Form("flyer"),
                 conn=Depends(db)):
    data = image.file.read(flyers.MAX_BYTES + 1)  # never read more than the cap plus one byte
    return _flyer_call(flyers.create_draft, conn, user_id, kind, data, lusaka_today())


@app.post("/flyers/{flyer_id}/confirm")
def confirm_flyer(flyer_id: int, body: FlyerConfirmIn, conn=Depends(db)):
    return _flyer_call(flyers.confirm, conn, body.user_id, flyer_id, body.model_dump(), lusaka_today())


# ---------- merchants (signup is open; verification is admin-only) ----------
def _merchant_call(fn, *args):
    try:
        return fn(*args)
    except merchants.MerchantError as e:
        raise HTTPException(status_code=merchants.STATUS.get(e.code, 400), detail=e.message)


def _require_admin(key):
    """Only a caller holding KONSYER_ADMIN_KEY (set in .env) is an admin. If the key is unset, admin is disabled."""
    expected = os.environ.get("KONSYER_ADMIN_KEY", "")
    if not expected:
        raise HTTPException(status_code=403, detail="Admin is disabled (no KONSYER_ADMIN_KEY set).")
    if not hmac.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Wrong admin key.")


@app.get("/merchants")
def my_merchants(user_id: int = 1, conn=Depends(db)):
    return {"merchants": merchants.list_for_owner(conn, user_id)}


@app.post("/merchants")
def register_merchant(body: MerchantIn, conn=Depends(db)):
    return _merchant_call(merchants.register, conn, body.user_id, body.model_dump())


@app.post("/merchants/{merchant_id}/listings")
def add_merchant_listing(merchant_id: int, body: MerchantListingIn, conn=Depends(db)):
    return _merchant_call(merchants.add_listing, conn, body.user_id, merchant_id, body.model_dump(),
                          lusaka_today())


@app.get("/merchants/{merchant_id}")
def get_merchant(merchant_id: int, user_id: int = 1, conn=Depends(db)):
    return _merchant_call(merchants.get_merchant, conn, user_id, merchant_id)


@app.post("/admin/merchants/{merchant_id}/verify")
def verify_merchant(merchant_id: int, x_admin_key: str = Header(default=""), conn=Depends(db)):
    _require_admin(x_admin_key)
    return _merchant_call(merchants.verify, conn, merchant_id)


@app.get("/admin/merchants")
def admin_merchants(x_admin_key: str = Header(default=""), conn=Depends(db)):
    _require_admin(x_admin_key)
    return {"merchants": merchants.admin_list(conn)}