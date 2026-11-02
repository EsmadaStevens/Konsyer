import json

from .. import contact
from ..timeutil import to_local


def plan_card(conn, plan_id):
    plan = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    rows = conn.execute("SELECT * FROM pending_actions WHERE plan_id=? ORDER BY id", (plan_id,)).fetchall()
    if not plan or not rows:
        return None
    items, sample = [], False
    for r in rows:
        p = json.loads(r["payload"])
        t = p.get("time_utc") or ""
        items.append({"action_id": r["id"], "label": p.get("label", r["kind"]), "time_utc": t,
                      "time": to_local(t).strftime("%H:%M") if t else "", "amount": r["amount"],
                      "decision": r["policy_decision"], "reason": r["policy_reason"], "status": r["status"]})
        if p.get("listing_id"):
            row = conn.execute("SELECT is_sample, status, photo IS NOT NULL AS has_photo FROM listings WHERE id=?",
                               (p["listing_id"],)).fetchone()
            sample = sample or bool(row and row["is_sample"])
            if row and row["has_photo"] and row["status"] == "verified":  # the seller's own picture, for people
                items[-1]["photo_url"] = f"/listings/{p['listing_id']}/photo"
    items.sort(key=lambda i: i["time_utc"])
    total = sum(i["amount"] for i in items if i["status"] != "denied")
    for i in items:
        del i["time_utc"]
    return {"type": "plan", "title": plan["title"] or "Your plan", "sample_data": sample,
            "budget": plan["budget"] if plan["budget"] is not None else total, "items": items, "total": total}


def contact_card(c):
    """Draft message plus links the user taps. Nothing has been sent. Outside (unverified) businesses also get an
    'invite to Konsyer' message, built by code from a template."""
    card = {"type": "contact", "name": c["name"], "message": c["message"], "channels": c["channels"],
            "phone": c.get("phone"), "price_text": c.get("price_text"), "price_for": c.get("price_for"),
            "corroborated": bool(c.get("corroborated")), "source_url": c.get("source_url"),
            "verified": bool(c.get("verified")), "sample_data": bool(c.get("sample_data"))}
    if not card["verified"]:
        invite = contact.build_invite(c["name"])
        card["invite_message"] = invite
        card["invite_channels"] = contact.channels(c.get("phone"), c.get("source_url"), invite)
    return card