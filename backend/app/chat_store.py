"""The conversation is kept so a reload does not lose it. Text and the static cards (web leads, message drafts) are
stored as they were shown. A plan card is NOT stored: it is rebuilt from its plan when the history is read, so its
Approve buttons and statuses are always current and can never show a stale state."""
import json

from .agent.cards import plan_card

KEEP = 60  # messages kept per user


def save_exchange(conn, user_id, user_text, reply, cards, plan_id, suggest_web):
    static = [c for c in cards if c.get("type") != "plan"]
    conn.execute("INSERT INTO chat_messages(user_id, role, text) VALUES (?, 'user', ?)", (user_id, user_text))
    conn.execute("INSERT INTO chat_messages(user_id, role, text, cards, plan_id, suggest_web) VALUES (?, 'assistant', ?, ?, ?, ?)",
                 (user_id, reply, json.dumps(static), plan_id, int(bool(suggest_web))))
    conn.execute("DELETE FROM chat_messages WHERE user_id=? AND id NOT IN "
                 "(SELECT id FROM chat_messages WHERE user_id=? ORDER BY id DESC LIMIT ?)", (user_id, user_id, KEEP))
    conn.commit()


def history(conn, user_id):
    rows = conn.execute("SELECT * FROM chat_messages WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
    out = []
    for i, r in enumerate(rows):
        cards = json.loads(r["cards"] or "[]")
        if r["plan_id"]:
            plan = plan_card(conn, r["plan_id"])
            if plan:
                cards.insert(0, plan)
        out.append({"from_user": r["role"] == "user", "text": r["text"], "cards": cards,
                    # the chip only makes sense on the newest message
                    "suggest_web": bool(r["suggest_web"]) and i == len(rows) - 1})
    return out


def clear(conn, user_id):
    conn.execute("DELETE FROM chat_messages WHERE user_id=?", (user_id,))
    conn.commit()
