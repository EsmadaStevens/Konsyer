"""Sample data for demos and tests. EVERYTHING here is fictional and flagged is_sample=1.
Coordinates are rough Lusaka-area points, not real venues."""
import json
from datetime import timedelta

from .timeutil import local_to_utc_iso


def seed(conn, today):
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return
    sat = today + timedelta(days=(5 - today.weekday()) % 7)
    sun = sat + timedelta(days=1)
    conn.execute("INSERT INTO users(id, name) VALUES (1, 'Demo user')")
    conn.execute(
        "INSERT INTO profiles(user_id, interests, home_lat, home_lng, autonomy, per_purchase_cap, daily_cap,"
        " auto_buy_under, ride_home_default) VALUES (1, ?, -15.3950, 28.3150, 'ask', 100000, 300000, 20000, 1)",
        (json.dumps(["active", "music"]),))
    conn.execute("INSERT INTO wallets(user_id, balance) VALUES (1, 500000)")

    def listing(name, ltype, price, tags, lat, lng, area, verified, trust, source="merchant",
                alcohol=0, desc="", slots=(), whatsapp=None):
        mid = conn.execute("INSERT INTO merchants(name, whatsapp, area, verified, is_sample) VALUES (?,?,?,?,1)",
                           (f"{name} (sample merchant)", whatsapp, area, verified)).lastrowid
        lid = conn.execute(
            "INSERT INTO listings(merchant_id, type, name, description, tags, lat, lng, area, price, serves_alcohol,"
            " source, status, trust_score, contact_whatsapp, is_sample) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (mid, ltype, name, desc, json.dumps(tags), lat, lng, area, price, alcohol, source,
             "verified" if verified else "unverified", trust, whatsapp)).lastrowid
        for (d, h1, m1, d2, h2, m2, cap) in slots:
            conn.execute("INSERT INTO slots(listing_id, starts_at, ends_at, capacity) VALUES (?,?,?,?)",
                         (lid, local_to_utc_iso(d, h1, m1), local_to_utc_iso(d2, h2, m2), cap))

    listing("Sample Go-Karting Track", "activity", 15000, ["active", "outdoor", "racing"], -15.4000, 28.3300,
            "Kabulonga", 1, 0.9, desc="Group karting sessions.",
            slots=[(sat, 14, 0, sat, 16, 0, 12), (sat, 16, 0, sat, 18, 0, 12)])
    listing("Sample Quad Bike Trail", "activity", 25000, ["active", "outdoor", "adventure"], -15.4500, 28.3400,
            "Ibex Hill", 1, 0.85, desc="Guided quad bike trail.", slots=[(sat, 9, 0, sat, 11, 30, 8)])
    listing("Sample Sunrise Hike", "activity", 5000, ["active", "outdoor", "hike"], -15.4300, 28.3100,
            "Woodlands", 1, 0.9, desc="Easy morning hike.", slots=[(sat, 6, 0, sat, 9, 0, 20)])
    listing("Sample Braai House", "dining", 18000, ["dinner", "grill", "food"], -15.4050, 28.3000,
            "Rhodes Park", 1, 0.85, alcohol=1, desc="Grill dinner with a bar.",
            slots=[(sat, 18, 30, sat, 20, 30, 20)])
    listing("Sample Afrobeats Night", "event", 15000, ["music", "dance", "nightlife"], -15.3900, 28.3100,
            "Roma", 0, 0.4, source="flyer", alcohol=1, desc="Late night event from a flyer; organizer unverified.",
            slots=[(sat, 21, 0, sun, 2, 0, 100)])
    listing("Sample Nail Studio", "service", 30000, ["nails", "beauty", "salon"], -15.4150, 28.2900,
            "Northmead", 0, 0.45, source="web_lead", desc="Gel nails. Contact details unverified.",
            slots=[(sat, 10, 0, sat, 11, 30, 1), (sat, 12, 0, sat, 13, 30, 1)])
    conn.commit()