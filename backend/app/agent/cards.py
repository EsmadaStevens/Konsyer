import json

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
            row = conn.execute("SELECT is_sample FROM listings WHERE id=?", (p["listing_id"],)).fetchone()
            sample = sample or bool(row and row["is_sample"])
    items.sort(key=lambda i: i["time_utc"])
    total = sum(i["amount"] for i in items if i["status"] != "denied")
    for i in items:
        del i["time_utc"]
    return {"type": "plan", "title": plan["title"] or "Your plan", "sample_data": sample,
            "budget": plan["budget"] if plan["budget"] is not None else total, "items": items, "total": total}