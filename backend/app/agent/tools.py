"""Agent tools. Read tools are free. propose_action is the ONLY write tool and it never moves money:
it records a pending action and asks the policy engine (plain code) for ALLOW / CONFIRM / DENY.
Amounts always come from the database, never from numbers the model supplies."""
import json
import os
import re
from datetime import timedelta

from .. import contact, policy, wallet
from .. import discovery as discovery_mod
from ..money import fmt_k
from ..providers.mock import MockRide
from ..timeutil import fmt_local, local_date, parse_utc, to_local, to_utc_iso

MAX_QTY = 50
MAX_DISCOVERY_CALLS = 3   # each call may be a paid web search; the loop also dedupes identical calls
MAX_DRAFTS = 5
_STOP = {"the", "and", "for", "with", "near", "some", "something", "any", "what", "whats", "lusaka", "this", "that"}
_LIVE = "('pending','approved','executed')"


def _show_phones():
    """DISCOVERY_SHOW_PHONES=0 hides web-lead phone numbers (and the WhatsApp/call buttons) if they prove unreliable."""
    return os.environ.get("DISCOVERY_SHOW_PHONES", "1") != "0"


def _tier(r):
    """Ranking tier, lower is shown first: verified merchants, then flyer/screenshot listings, then web leads."""
    if r["status"] == "verified":
        return 0
    return 2 if r["source"] == "web_lead" else 1


def _obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


def _fn(name, desc, params):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


_INT, _STR = {"type": "integer"}, {"type": "string"}
TOOLS = [
    _fn("search_listings", "Search bookable listings (events, activities, dining, services) in Lusaka. Prices are per person in ngwee.",
        _obj({"query": {**_STR, "description": "Keywords, e.g. 'active' or 'nails'"},
              "type": {"type": "string", "enum": ["event", "activity", "dining", "service"]},
              "date": {**_STR, "description": "YYYY-MM-DD"},
              "max_price_ngwee": {**_INT, "description": "Max price per person in ngwee"},
              "tags": {"type": "array", "items": _STR}})),
    _fn("get_listing", "Get full details and open slots for one listing.", _obj({"listing_id": _INT}, ["listing_id"])),
    _fn("check_trust", "Check how trustworthy a listing/seller is and whether paying them needs the user's approval.",
        _obj({"listing_id": _INT}, ["listing_id"])),
    _fn("get_profile", "Get the user's interests and spending limits.", _obj({})),
    _fn("get_wallet", "Get the user's wallet balance and what they spent today.", _obj({})),
    _fn("solve_budget", "Compute totals for a plan. ALWAYS use this for any sum. Amounts in ngwee.",
        _obj({"budget_ngwee": _INT, "party_size": _INT,
              "items": {"type": "array", "items": _obj(
                  {"label": _STR, "unit_price_ngwee": _INT, "per_person": {"type": "boolean"}},
                  ["label", "unit_price_ngwee"])}}, ["budget_ngwee", "party_size", "items"])),
    _fn("estimate_ride", "Estimate a simulated ride home from a listing's location to the user's home.",
        _obj({"listing_id": _INT}, ["listing_id"])),
    _fn("propose_action", "Propose booking a listing slot for the party. This only PROPOSES; the user must approve in the app.",
        _obj({"kind": {"type": "string", "enum": ["book_listing"]}, "listing_id": _INT, "slot_id": _INT,
              "qty": {**_INT, "description": "Number of people/tickets"},
              "plan_title": _STR,
              "plan_budget_ngwee": {**_INT, "description": "The user's stated total budget for this plan"}},
            ["kind", "listing_id", "slot_id", "qty"])),
    _fn("discover_services", "Search the web for businesses nobody has registered here (for example hair salons). Results are "
        "UNVERIFIED leads: they cannot be booked or paid for here, and a price is shown only if the source states one.",
        _obj({"service": {**_STR, "description": "e.g. 'hair salon' or 'barber'"},
              "area": {**_STR, "description": "e.g. 'Kabulonga'. Defaults to Lusaka."}}, ["service"])),
    _fn("draft_contact_message", "Draft a message to a seller and prepare WhatsApp/Messenger links. It only DRAFTS: the user taps send "
        "in the app. Use lead_id from discover_services (valid only in this reply) OR listing_id of a listing with contact details.",
        _obj({"details": {**_STR, "description": "What the user wants, e.g. 'gel nails Saturday afternoon, budget K300'. No links."},
              "lead_id": _STR, "listing_id": _INT}, ["details"])),
]


class Tools:
    def __init__(self, conn, user_id, today, ride=None, discovery="default"):
        self.conn, self.user_id, self.today = conn, user_id, today
        self.ride = ride or MockRide()
        self.discovery = discovery_mod.get_provider() if discovery == "default" else discovery
        self.contacts, self._searches = [], 0
        self.plan_id = None
        self.proposed = []
        self.names = {t["function"]["name"] for t in TOOLS}

    def call(self, name, args):
        if name not in self.names:
            return {"error": f"unknown tool {name!r}"}
        if not isinstance(args, dict):
            return {"error": "arguments must be a JSON object"}
        try:
            return getattr(self, name)(**args)
        except (TypeError, KeyError, ValueError) as e:
            return {"error": f"bad arguments: {e}"}

    # ---------- helpers ----------
    def _profile(self):
        return self.conn.execute("SELECT * FROM profiles WHERE user_id=?", (self.user_id,)).fetchone()

    def _balance(self):
        r = self.conn.execute("SELECT balance FROM wallets WHERE user_id=?", (self.user_id,)).fetchone()
        return r["balance"] if r else 0

    def _spent_today(self):
        return wallet.spent_today(self.conn, self.user_id, self.today)

    def _slots(self, listing_id):
        rows = self.conn.execute("SELECT * FROM slots WHERE listing_id=? ORDER BY starts_at", (listing_id,)).fetchall()
        return [{"slot_id": s["id"], "starts": fmt_local(s["starts_at"]),
                 "ends": fmt_local(s["ends_at"]) if s["ends_at"] else None,
                 "spots_left": s["capacity"] - s["booked"], "_starts_utc": s["starts_at"]} for s in rows]

    def _listing_dict(self, r, full=False):
        slots = self._slots(r["id"])
        d = {"listing_id": r["id"], "name": r["name"], "type": r["type"], "area": r["area"],
             "price_per_person_ngwee": r["price"], "price_display": fmt_k(r["price"]) if r["price"] is not None else None,
             "serves_alcohol": bool(r["serves_alcohol"]), "status": r["status"], "trust_score": r["trust_score"],
             "verified": r["status"] == "verified", "source": r["source"],
             "is_sample": bool(r["is_sample"]), "has_whatsapp": bool(r["contact_whatsapp"]),
             "slots": [{k: v for k, v in s.items() if not k.startswith("_")} for s in slots]}
        if full:
            d["description"] = r["description"]
        return d

    def _plan(self, title, budget):
        if self.plan_id is None:
            b = int(budget) if budget is not None and int(budget) > 0 else None
            self.plan_id = self.conn.execute("INSERT INTO plans(user_id, title, budget) VALUES (?,?,?)",
                                             (self.user_id, (title or "Your plan")[:80], b)).lastrowid
        return self.plan_id

    def _plan_remaining(self, plan_id, exclude=None):
        p = self.conn.execute("SELECT budget FROM plans WHERE id=?", (plan_id,)).fetchone()
        if p is None or p["budget"] is None:
            return None
        used = self.conn.execute(
            f"SELECT COALESCE(SUM(amount),0) FROM pending_actions WHERE plan_id=? AND status IN {_LIVE} AND id IS NOT ?",
            (plan_id, exclude)).fetchone()[0]
        return p["budget"] - used

    def _decide(self, amount, merchant_id, verified, trust, plan_id, exclude=None):
        p = self._profile()
        limits = policy.Limits(p["autonomy"], p["per_purchase_cap"], p["daily_cap"], p["auto_buy_under"],
                               frozenset(json.loads(p["pre_approved_merchants"] or "[]")))
        ctx = policy.Context(self._balance(), self._spent_today(), self._plan_remaining(plan_id, exclude))
        return policy.decide(limits, ctx, policy.Action(amount, merchant_id, verified, trust))

    def _store(self, action_id, d):
        status = {"ALLOW": "approved", "CONFIRM": "pending", "DENY": "denied"}[d.verdict.value]
        self.conn.execute("UPDATE pending_actions SET status=?, policy_decision=?, policy_rule=?, policy_reason=? WHERE id=?",
                          (status, d.verdict.value, d.rule, d.reason, action_id))
        self.conn.execute("INSERT INTO audit_log(user_id, pending_action_id, event, why, rule) VALUES (?,?,?,?,?)",
                          (self.user_id, action_id, f"policy_{d.verdict.value.lower()}", d.reason, d.rule))

    # ---------- read tools ----------
    def search_listings(self, query="", type=None, date=None, max_price_ngwee=None, tags=None, **_):
        words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) > 2 and w not in _STOP]
        tagset = [t.lower() for t in (tags or []) if isinstance(t, str)]
        found = []
        for r in self.conn.execute("SELECT * FROM listings").fetchall():
            if r["status"] == "claimed":
                continue  # submitted by a business but not yet verified: hidden from the agent
            if type and r["type"] != type:
                continue
            if max_price_ngwee is not None and r["price"] is not None and r["price"] > int(max_price_ngwee):
                continue
            slots = self._slots(r["id"])
            if date and slots and not any(local_date(s["_starts_utc"]).isoformat() == date for s in slots):
                continue
            hay = f"{r['name']} {r['description'] or ''} {r['tags']} {r['type']}".lower()
            score = sum(w in hay for w in words) + sum(t in hay for t in tagset)
            if (words or tagset) and score == 0:
                continue
            found.append((score, r))
        found.sort(key=lambda x: (_tier(x[1]), -x[0], -x[1]["trust_score"], x[1]["id"]))
        out = [self._listing_dict(r) for _, r in found[:8]]
        return {"count": len(out), "listings": out}

    def get_listing(self, listing_id, **_):
        r = self.conn.execute("SELECT * FROM listings WHERE id=?", (int(listing_id),)).fetchone()
        if not r or r["status"] == "claimed":
            return {"error": "listing not found"}
        return self._listing_dict(r, full=True)

    def check_trust(self, listing_id, **_):
        r = self.conn.execute("SELECT l.*, m.verified AS mv FROM listings l LEFT JOIN merchants m ON m.id=l.merchant_id WHERE l.id=?",
                              (int(listing_id),)).fetchone()
        if not r:
            return {"error": "listing not found"}
        reasons = [f"listing status: {r['status']}", f"source: {r['source']}",
                   "merchant verified" if r["mv"] else "merchant NOT verified"]
        return {"listing_id": r["id"], "trust_score": r["trust_score"], "status": r["status"], "reasons": reasons,
                "payment_needs_user_approval": r["status"] != "verified" or r["trust_score"] < policy.MIN_TRUST}

    def get_profile(self, **_):
        p = self._profile()
        return {"interests": json.loads(p["interests"] or "[]"), "autonomy": p["autonomy"],
                "per_purchase_cap": fmt_k(p["per_purchase_cap"]), "daily_cap": fmt_k(p["daily_cap"]),
                "ride_home_default": bool(p["ride_home_default"])}

    def get_wallet(self, **_):
        return {"balance_ngwee": self._balance(), "balance_display": fmt_k(self._balance()),
                "spent_today_display": fmt_k(self._spent_today())}

    def solve_budget(self, budget_ngwee, party_size, items, **_):
        b, ps = int(budget_ngwee), int(party_size)
        if not 1 <= ps <= MAX_QTY:
            return {"error": f"party_size must be 1 to {MAX_QTY}"}
        lines, total = [], 0
        for it in items or []:
            unit = int(it["unit_price_ngwee"])
            cost = unit * ps if it.get("per_person", True) else unit
            total += cost
            lines.append({"label": it.get("label"), "cost_ngwee": cost, "cost_display": fmt_k(cost)})
        return {"total_ngwee": total, "total_display": fmt_k(total), "budget_display": fmt_k(b),
                "remaining_ngwee": b - total, "remaining_display": fmt_k(b - total), "fits": total <= b, "lines": lines}

    def estimate_ride(self, listing_id, **_):
        r = self.conn.execute("SELECT lat, lng FROM listings WHERE id=?", (int(listing_id),)).fetchone()
        p = self._profile()
        if not r or r["lat"] is None or p["home_lat"] is None:
            return {"error": "location unknown, cannot estimate a ride"}
        q = self.ride.estimate(r["lat"], r["lng"], p["home_lat"], p["home_lng"])
        return {"price_ngwee": q.price, "price_display": fmt_k(q.price), "eta_minutes": q.eta_minutes,
                "provider": q.provider, "simulated": True}

    # ---------- the only write tool ----------
    def propose_action(self, kind, listing_id, slot_id, qty, plan_title=None, plan_budget_ngwee=None, **_):
        if kind != "book_listing":
            return {"error": "unsupported action kind"}
        qty = int(qty)
        if not 1 <= qty <= MAX_QTY:
            return {"error": f"qty must be 1 to {MAX_QTY}"}
        l = self.conn.execute("SELECT * FROM listings WHERE id=?", (int(listing_id),)).fetchone()
        s = self.conn.execute("SELECT * FROM slots WHERE id=?", (int(slot_id),)).fetchone()
        if not l:
            return {"error": "listing not found"}
        if l["status"] == "claimed":
            return {"error": "this listing is awaiting verification and cannot be booked"}
        if l["source"] == "web_lead":
            return {"error": "this is an unverified web lead and cannot be booked; the user can contact the seller"}
        if not s or s["listing_id"] != l["id"]:
            return {"error": "that slot does not belong to that listing"}
        if s["capacity"] - s["booked"] < qty:
            return {"error": f"only {s['capacity'] - s['booked']} spots left"}
        if l["price"] is None:
            return {"error": "price not listed; the user must ask the seller"}
        amount = l["price"] * qty  # computed here, never taken from the model
        plan_id = self._plan(plan_title, plan_budget_ngwee)
        payload = {"kind": "book_listing", "listing_id": l["id"], "slot_id": s["id"], "qty": qty,
                   "label": f"{l['name']} x{qty}", "time_utc": s["starts_at"]}
        for row in self.conn.execute(f"SELECT id, payload FROM pending_actions WHERE plan_id=? AND kind='book_listing' AND status IN {_LIVE}", (plan_id,)):
            old = json.loads(row["payload"])
            if (old["listing_id"], old["slot_id"], old["qty"]) == (l["id"], s["id"], qty):
                return {"action_id": row["id"], "note": "already proposed"}
        aid = self.conn.execute(
            "INSERT INTO pending_actions(user_id, plan_id, kind, payload, amount) VALUES (?,?,?,?,?)",
            (self.user_id, plan_id, "book_listing", json.dumps(payload), amount)).lastrowid
        self.conn.execute("INSERT INTO audit_log(user_id, pending_action_id, event, why) VALUES (?,?,?,?)",
                          (self.user_id, aid, "proposed", f"Agent proposed {payload['label']} for {fmt_k(amount)}"))
        d = self._decide(amount, l["merchant_id"], l["status"] == "verified", l["trust_score"], plan_id, exclude=aid)
        self._store(aid, d)
        self.proposed.append(aid)
        out = {"action_id": aid, "amount_display": fmt_k(amount), "decision": d.verdict.value, "reason": d.reason,
               "status": {"ALLOW": "approved by your rules", "CONFIRM": "waiting for the user to approve in the app",
                          "DENY": "blocked by the user's limits; nothing was booked"}[d.verdict.value]}
        ride = self._auto_ride(l, s, plan_id)
        if ride:
            out["ride_home_auto_added"] = ride
        self.conn.commit()
        return out

    def _auto_ride(self, l, s, plan_id):
        p = self._profile()
        end = s["ends_at"] or l["ends_at"]
        alcohol = bool(l["serves_alcohol"])
        late = bool(end) and (to_local(end).hour >= 21 or to_local(end).date() > to_local(s["starts_at"]).date())
        if not p["ride_home_default"] or not (alcohol or late):
            return None
        if l["lat"] is None or p["home_lat"] is None:
            return {"skipped": "location unknown"}
        base = parse_utc(end) if end else parse_utc(s["starts_at"]) + timedelta(hours=3)
        pickup = to_utc_iso(base + timedelta(minutes=15))
        q = self.ride.estimate(l["lat"], l["lng"], p["home_lat"], p["home_lng"])
        payload = {"kind": "ride_home", "label": f"Ride home ({q.provider})", "pickup_at": pickup,
                   "time_utc": pickup, "from_listing_id": l["id"], "simulated": True}
        ex = self.conn.execute(f"SELECT id, payload FROM pending_actions WHERE plan_id=? AND kind='ride_home' AND status IN {_LIVE}", (plan_id,)).fetchone()
        if ex:
            if json.loads(ex["payload"])["pickup_at"] >= pickup:
                return {"already_planned": True}
            aid = ex["id"]  # one ride home per plan: keep the latest pickup
            self.conn.execute("UPDATE pending_actions SET payload=?, amount=? WHERE id=?", (json.dumps(payload), q.price, aid))
        else:
            aid = self.conn.execute("INSERT INTO pending_actions(user_id, plan_id, kind, payload, amount) VALUES (?,?,?,?,?)",
                                    (self.user_id, plan_id, "ride_home", json.dumps(payload), q.price)).lastrowid
            self.conn.execute("INSERT INTO audit_log(user_id, pending_action_id, event, why) VALUES (?,?,?,?)",
                              (self.user_id, aid, "ride_added",
                               "Ride home added automatically because this booking serves alcohol or ends late"))
            self.proposed.append(aid)
        d = self._decide(q.price, None, True, 1.0, plan_id, exclude=aid)
        self._store(aid, d)
        return {"action_id": aid, "pickup": fmt_local(pickup), "price_display": fmt_k(q.price),
                "provider": q.provider, "decision": d.verdict.value}

    # ---------- discovery and contact (read-only: nothing is booked, paid or sent) ----------
    def discover_services(self, service, area=None, **_):
        service, area = contact.one_line(service, 60), contact.one_line(area, 60) or "Lusaka"
        if not service:
            return {"error": "service is required"}
        if self.discovery is None:
            return {"error": "web discovery is not available"}
        if self._searches >= MAX_DISCOVERY_CALLS:
            return {"error": "search limit reached for this reply; use the results you already have"}
        self._searches += 1
        try:
            rows, _cached = discovery_mod.find_or_search(self.conn, self.discovery, service, area)
        except Exception:  # a provider must never break the chat
            return {"error": "web discovery is unavailable right now"}
        discovery_mod.remember(self.conn, self.user_id, rows)
        out = []
        for r in rows:
            phone = r["phone"] if _show_phones() else None
            out.append({"lead_id": f"L{r['id']}", "name": r["name"], "area": r["area"], "platform": r["platform"],
                        "source_host": contact.host_of(r["source_url"]),
                        "has_whatsapp_number": contact.phone_kind(phone) == "mobile",
                        "price_text": r["price_text"] or "not listed: ask the seller", "price_for": r["price_for"],
                        "number_on_several_sites": bool(r["corroborated"]),
                        "verified": False, "source": "web_lead", "is_sample": bool(r["is_sample"])})
        return {"count": len(out), "leads": out,
                "note": "Unverified web results from directories and social pages, which can be out of date. They cannot be "
                        "booked or paid for here. price_text is only what a page said, and may cover one specific service: "
                        "treat it as a hint and tell the user to confirm. To help the user reach one, call draft_contact_message."}

    def draft_contact_message(self, details, lead_id=None, listing_id=None, **_):
        if (lead_id is None) == (listing_id is None):
            return {"error": "give exactly one of lead_id or listing_id"}
        if len(self.contacts) >= MAX_DRAFTS:
            return {"error": "draft limit reached for this reply"}
        if lead_id is not None:
            m = re.fullmatch(r"L(\d{1,9})", str(lead_id).strip().upper())
            lead = discovery_mod.get_lead(self.conn, int(m.group(1))) if m else None
            if lead is None:
                return {"error": "unknown or expired lead_id (leads last 24 hours); search again"}
            name, phone, url = lead["name"], (lead["phone"] if _show_phones() else None), lead["source_url"]
            verified, sample, price_text = False, bool(lead["is_sample"]), lead["price_text"]
            price_for, corroborated = lead["price_for"], bool(lead["corroborated"])
        else:
            r = self.conn.execute("SELECT * FROM listings WHERE id=?", (int(listing_id),)).fetchone()
            if not r or r["status"] == "claimed":
                return {"error": "listing not found"}
            name, phone, url = r["name"], contact.parse_zm_phone(r["contact_whatsapp"]), None
            verified, sample, price_text = r["status"] == "verified", bool(r["is_sample"]), None
            price_for, corroborated = None, False
        message = contact.build_message(name, details)
        chans = contact.channels(phone, url, message)
        if not chans:
            return {"error": "no contact details for this seller; tell the user to ask them directly"}
        self.contacts.append({"name": name, "message": message, "channels": chans, "phone": phone,
                              "source_url": url, "price_text": price_text, "price_for": price_for,
                              "corroborated": corroborated, "verified": verified, "sample_data": sample})
        self.conn.execute("INSERT INTO audit_log(user_id, event, why, rule) VALUES (?,?,?,?)",
                          (self.user_id, "contact_drafted",
                           f"Drafted a message to '{name}'. Nothing was sent: the user taps send in their own app.",
                           "draft_only"))
        self.conn.commit()
        return {"drafted": True, "channels": [c["kind"] for c in chans],
                "note": "Nothing has been sent. The user taps send in the app and should check the number or page first."}