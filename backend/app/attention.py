"""What needs a person's attention right now, in one list: a seller's offer of a new time, a photo claim waiting for a
reply, a claim that was just decided. Plain code that reads the same data the Held payments and Business screens show;
it changes nothing."""
from datetime import timedelta

from . import disputes, escrow


def for_user(conn, user_id, at=None):
    at = at or escrow.now(conn)
    items = []

    # as a customer
    for v in disputes.attach(conn, escrow.list_for_user(conn, user_id, at)):
        label = v["label"]
        d = v.get("dispute")
        if v["state"] == "held" and v.get("offer"):
            items.append({"kind": "action", "target": "payments",
                          "text": f"{label}: the seller offered a new time ({v['offer']['starts']}). Accept it or take a refund."})
        elif v["state"] == "held" and d and d["can_retry"]:
            items.append({"kind": "action", "target": "payments",
                          "text": f"{label}: the photo was not clear enough. Try another photo."})
        elif v["state"] == "held" and d and d["status"] == "open":
            who = "Konsyer" if d["stage"] == "konsyer" else "the seller"
            items.append({"kind": "info", "target": "payments", "text": f"{label}: your photo claim is with {who}."})

    cutoff = escrow._iso(at - timedelta(hours=48))  # a decision stays visible for two days
    for r in conn.execute(
            "SELECT d.status, v.label FROM disputes d JOIN vouchers v ON v.id=d.voucher_id "
            "WHERE d.user_id=? AND d.status IN ('refunded','rejected') AND d.resolved_at >= ? ORDER BY d.id DESC",
            (user_id, cutoff)):
        outcome = "refunded in full" if r["status"] == "refunded" else "not accepted, so the payment continues as normal"
        items.append({"kind": "info", "target": "payments", "text": f"{r['label']}: your claim was decided, {outcome}."})

    # as a business owner
    owned = conn.execute("SELECT id FROM merchants WHERE owner_user_id=?", (user_id,)).fetchall()
    for m in owned:
        for v in disputes.attach(conn, escrow.list_for_merchant(conn, user_id, m["id"], at), merchant=True):
            if v.get("can_resolve"):
                items.append({"kind": "action", "target": "business",
                              "text": f"A customer says {v['label']} is not as described. Refund them or contest it."})

    items.sort(key=lambda i: i["kind"] != "action")
    return {"count": sum(1 for i in items if i["kind"] == "action"), "items": items}
