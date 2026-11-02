"""API entry point. Run from backend/:  uvicorn app.main:app --reload --port 8000
Set KONSYER_STUB=1 to return a canned reply without calling the model."""
import hmac
import os
import uuid
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import config  # noqa: F401  (loads .env)
from . import activity, disputes, escrow, executor, flyers, leads, listing_drafts, merchants, reschedule, wallet
from .agent.cards import contact_card, plan_card
from .agent.loop import run_agent
from .db import get_conn, init_db
from .llm import LLMError
from .seed import seed
from .timeutil import lusaka_today

app = FastAPI(title="Konsyer API")
activity.install()
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


class HoldIn(BaseModel):
    user_id: int = 1
    amount_ngwee: int
    what: Optional[str] = None
    idempotency_key: Optional[str] = None


class LeadContactIn(BaseModel):
    user_id: int = 1
    details: str


class LeadFeedbackIn(BaseModel):
    user_id: int = 1
    status: Optional[str] = None
    price: Optional[str] = None


class ResolveIn(BaseModel):
    user_id: int = 1
    decision: str


class AdminResolveIn(BaseModel):
    decision: str


class ContestIn(BaseModel):
    user_id: int = 1
    note: str


class PayoutCheckIn(BaseModel):
    user_id: int = 1
    payout_number: str


class ReleaseIn(BaseModel):
    user_id: int = 1
    payout_number: Optional[str] = None
    confirmed_number: Optional[str] = None
    accept_warning: bool = False


class ReportIn(BaseModel):
    user_id: int = 1
    reason: str


class OfferIn(BaseModel):
    user_id: int = 1
    slot_id: int


class AdvanceIn(BaseModel):
    hours: int


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
    lead_ids = leads.ids_from_trace(r["trace"])
    if not r.get("contacts"):  # the model may answer from remembered leads without searching again
        lead_ids += [i for i in leads.mentioned_ids(conn, body.user_id, r["reply"]) if i not in lead_ids]
    lead_card = leads.card(conn, body.user_id, lead_ids)
    cards = ([card] if card else []) + ([lead_card] if lead_card else []) + \
        [contact_card(c) for c in r.get("contacts", [])]
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
    escrow.sweep(conn, user_id)
    return {"sandbox": True, "balance": wallet.balance(conn, user_id), "held": escrow.held_total(conn, user_id),
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


# ---------- listing drafts from a picture: registered business owners only ----------
def _draft_call(fn, *args):
    try:
        return fn(*args)
    except flyers.FlyerError as e:
        raise HTTPException(status_code=flyers.STATUS.get(e.code, 400), detail=e.message)


@app.post("/merchants/{merchant_id}/draft-from-flyer")
def draft_from_flyer(merchant_id: int, image: UploadFile = File(...), user_id: int = Form(1), conn=Depends(db)):
    data = image.file.read(flyers.MAX_BYTES + 1)  # never read more than the cap plus one byte
    return _draft_call(listing_drafts.from_flyer, conn, user_id, merchant_id, data, lusaka_today())


@app.post("/merchants/{merchant_id}/draft-from-photo")
def draft_from_photo(merchant_id: int, image: UploadFile = File(...), user_id: int = Form(1), conn=Depends(db)):
    data = image.file.read(listing_drafts.MAX_PHOTO_BYTES + 1)
    return _draft_call(listing_drafts.from_photo, conn, user_id, merchant_id, data)


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


@app.post("/listings/{listing_id}/photo")
def set_listing_photo(listing_id: int, image: UploadFile = File(...), user_id: int = Form(1), conn=Depends(db)):
    data = image.file.read(merchants.MAX_LISTING_PHOTO + 1)
    return _merchant_call(merchants.set_photo, conn, user_id, listing_id, data)


@app.post("/listings/{listing_id}/photo/remove")
def remove_listing_photo(listing_id: int, body: ActionIn, conn=Depends(db)):
    return _merchant_call(merchants.remove_photo, conn, body.user_id, listing_id)


@app.get("/listings/{listing_id}/photo")
def listing_photo(listing_id: int, user_id: int = 0, x_admin_key: str = Header(default=""), conn=Depends(db)):
    """Public for a verified listing; the owner and an admin can also see it earlier."""
    expected = os.environ.get("KONSYER_ADMIN_KEY", "")
    admin = bool(expected) and hmac.compare_digest(x_admin_key.encode(), expected.encode())
    data, ext = _merchant_call(merchants.get_photo, conn, listing_id, user_id or None, admin)
    return Response(content=data, media_type={".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
                    .get(ext, "application/octet-stream"), headers={"Cache-Control": "no-store"})


@app.post("/admin/merchants/{merchant_id}/verify")
def verify_merchant(merchant_id: int, x_admin_key: str = Header(default=""), conn=Depends(db)):
    _require_admin(x_admin_key)
    return _merchant_call(merchants.verify, conn, merchant_id)


@app.get("/admin/merchants")
def admin_merchants(x_admin_key: str = Header(default=""), conn=Depends(db)):
    _require_admin(x_admin_key)
    return {"merchants": merchants.admin_list(conn)}


# ---------- escrow vouchers (sandbox money held between a user and a seller; payouts are simulated) ----------
def _escrow_call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except escrow.EscrowError as e:
        raise HTTPException(status_code=escrow.STATUS.get(e.code, 400), detail=e.message)


@app.get("/vouchers")
def my_vouchers(user_id: int = 1, conn=Depends(db)):
    return {"sandbox": True, "held": escrow.held_total(conn, user_id),
            "vouchers": disputes.attach(conn, _escrow_call(escrow.list_for_user, conn, user_id))}


@app.post("/leads/{lead_id}/hold")
def hold_for_lead(lead_id: int, body: HoldIn, conn=Depends(db)):
    return _escrow_call(escrow.create_lead_hold, conn, body.user_id, lead_id, body.amount_ngwee, body.what,
                        body.idempotency_key)


@app.post("/vouchers/{voucher_id}/payout-check")
def voucher_payout_check(voucher_id: int, body: PayoutCheckIn, conn=Depends(db)):
    return _escrow_call(escrow.payout_check, conn, body.user_id, voucher_id, body.payout_number)


@app.post("/vouchers/{voucher_id}/release")
def release_voucher(voucher_id: int, body: ReleaseIn, conn=Depends(db)):
    return _escrow_call(escrow.release, conn, body.user_id, voucher_id, body.payout_number, body.confirmed_number,
                        body.accept_warning)


@app.post("/vouchers/{voucher_id}/cancel")
def cancel_voucher(voucher_id: int, body: ActionIn, conn=Depends(db)):
    return _escrow_call(escrow.cancel, conn, body.user_id, voucher_id)


@app.post("/vouchers/{voucher_id}/report")
def report_voucher(voucher_id: int, body: ReportIn, conn=Depends(db)):
    return _escrow_call(escrow.report, conn, body.user_id, voucher_id, body.reason)


@app.get("/merchants/{merchant_id}/vouchers")
def merchant_vouchers(merchant_id: int, user_id: int = 1, conn=Depends(db)):
    return {"vouchers": disputes.attach(conn, _escrow_call(escrow.list_for_merchant, conn, user_id, merchant_id),
                                        merchant=True)}


@app.post("/vouchers/{voucher_id}/merchant-confirm")
def voucher_merchant_confirm(voucher_id: int, body: ActionIn, conn=Depends(db)):
    return _escrow_call(escrow.merchant_confirm, conn, body.user_id, voucher_id)


@app.post("/vouchers/{voucher_id}/merchant-cancel")
def voucher_merchant_cancel(voucher_id: int, body: ActionIn, conn=Depends(db)):
    return _escrow_call(escrow.merchant_cancel, conn, body.user_id, voucher_id)


@app.get("/vouchers/{voucher_id}/reschedule-options")
def reschedule_options(voucher_id: int, user_id: int = 1, conn=Depends(db)):
    return _escrow_call(reschedule.options, conn, user_id, voucher_id)


@app.post("/vouchers/{voucher_id}/merchant-offer")
def voucher_merchant_offer(voucher_id: int, body: OfferIn, conn=Depends(db)):
    return _escrow_call(reschedule.offer, conn, body.user_id, voucher_id, body.slot_id)


@app.post("/vouchers/{voucher_id}/accept-offer")
def voucher_accept_offer(voucher_id: int, body: ActionIn, conn=Depends(db)):
    return _escrow_call(reschedule.accept, conn, body.user_id, voucher_id)


@app.post("/vouchers/{voucher_id}/decline-offer")
def voucher_decline_offer(voucher_id: int, body: ActionIn, conn=Depends(db)):
    return _escrow_call(reschedule.decline, conn, body.user_id, voucher_id)


@app.post("/admin/advance-time")
def advance_time(body: AdvanceIn, x_admin_key: str = Header(default=""), conn=Depends(db)):
    """DEMO ONLY: move the app's clock forward so a video can show the 24-hour auto-release."""
    _require_admin(x_admin_key)
    return _escrow_call(escrow.advance, conn, body.hours)


# ---------- web leads: contact drafts without the agent, and "did they reply?" reports ----------
def _lead_call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except leads.LeadError as e:
        raise HTTPException(status_code=leads.STATUS.get(e.code, 400), detail=e.message)


@app.post("/leads/{lead_id}/contact")
def lead_contact(lead_id: int, body: LeadContactIn, conn=Depends(db)):
    return contact_card(_lead_call(leads.draft_contact, conn, body.user_id, lead_id, body.details))


@app.post("/leads/{lead_id}/feedback")
def lead_feedback(lead_id: int, body: LeadFeedbackIn, conn=Depends(db)):
    return _lead_call(leads.record_feedback, conn, body.user_id, lead_id, body.status, body.price)


# ---------- photo disputes: the model gives a recommendation, a person decides ----------
def _dispute_call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except disputes.DisputeError as e:
        raise HTTPException(status_code=disputes.STATUS.get(e.code, 400), detail=e.message)


@app.post("/vouchers/{voucher_id}/dispute")
def dispute_voucher(voucher_id: int, photo: UploadFile = File(...), note: str = Form(""), user_id: int = Form(1),
                    conn=Depends(db)):
    if os.environ.get("KONSYER_STUB") == "1" or not os.environ.get("LLM_API_KEY"):
        raise HTTPException(status_code=503, detail="The photo check needs a model key. See the README.")
    data = photo.file.read(disputes.MAX_BYTES + 1)  # never read more than the cap plus one byte
    return _dispute_call(disputes.open_dispute, conn, user_id, voucher_id, note, data)


@app.post("/disputes/{dispute_id}/resolve")
def resolve_dispute_as_business(dispute_id: int, body: ResolveIn, conn=Depends(db)):
    return _dispute_call(disputes.resolve, conn, body.user_id, dispute_id, body.decision)


@app.post("/disputes/{dispute_id}/contest")
def contest_dispute(dispute_id: int, body: ContestIn, conn=Depends(db)):
    """The seller disagrees: the case goes to Konsyer. The seller cannot reject a claim themselves."""
    return _dispute_call(disputes.contest, conn, body.user_id, dispute_id, body.note)


_PHOTO_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _photo_response(data: bytes):
    return Response(content=data, media_type=_PHOTO_TYPES.get(disputes.detect_image(data), "application/octet-stream"),
                    headers={"Cache-Control": "no-store"})


@app.get("/disputes/{dispute_id}/photo")
def dispute_photo_for_seller(dispute_id: int, user_id: int = 1, conn=Depends(db)):
    return _photo_response(_dispute_call(disputes.photo, conn, user_id, dispute_id))


@app.get("/admin/disputes/{dispute_id}/photo")
def dispute_photo_for_admin(dispute_id: int, x_admin_key: str = Header(default=""), conn=Depends(db)):
    _require_admin(x_admin_key)
    return _photo_response(_dispute_call(disputes.photo, conn, 0, dispute_id, True))


@app.get("/admin/disputes")
def admin_disputes(x_admin_key: str = Header(default=""), conn=Depends(db)):
    _require_admin(x_admin_key)
    return {"disputes": disputes.list_open(conn)}


@app.post("/admin/disputes/{dispute_id}/resolve")
def resolve_dispute_as_admin(dispute_id: int, body: AdminResolveIn, x_admin_key: str = Header(default=""),
                             conn=Depends(db)):
    _require_admin(x_admin_key)
    return _dispute_call(disputes.resolve, conn, 0, dispute_id, body.decision, True)


# ---------- activity: what the models did, and what rules and people decided ----------
@app.get("/activity")
def get_activity(user_id: int = 1, limit: int = 40, conn=Depends(db)):
    return activity.view(conn, user_id, limit)
