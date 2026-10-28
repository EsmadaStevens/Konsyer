"""Talk to the real agent from the terminal (uses your Baseten key and a throwaway in-memory database).
  python -m scripts.chat_cli "Saturday, 5 of us, K2,000, something active, dinner, safe ride home"
Prints the reply, which tools the agent used, and the pending actions with the policy decision."""
import json
import sys
import time

from app.agent.loop import run_agent
from app.db import get_conn, init_db
from app.money import fmt_k
from app.seed import seed
from app.timeutil import lusaka_today

msg = " ".join(sys.argv[1:]) or "Saturday, 5 of us, K2,000, something active, dinner, safe ride home"
conn = get_conn(":memory:")
init_db(conn)
seed(conn, lusaka_today())
t0 = time.time()
r = run_agent(conn, 1, msg)
print(f"\nYOU: {msg}\n\nKONSYER: {r['reply']}\n\n--- tools used ({r['steps']} model calls, {time.time() - t0:.1f}s) ---")
for t in r["trace"]:
    print(f"  {t['tool']}({json.dumps(t['args'])[:110]})")
print("\n--- pending actions ---")
for a in conn.execute("SELECT * FROM pending_actions ORDER BY id"):
    print(f"  #{a['id']} {a['kind']:12s} {fmt_k(a['amount']):>8s}  {a['policy_decision']:8s} {a['policy_rule']}  | {a['policy_reason']}")