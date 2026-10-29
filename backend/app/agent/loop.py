"""The agent loop: model <-> tools, capped at max_steps. The model can only propose; policy code decides."""
import json
import os

from .. import llm
from ..timeutil import lusaka_today
from .prompts import system_prompt
from .tools import TOOLS, Tools

FALLBACK = "Sorry, I couldn't finish that. Could you try rephrasing?"


def _clean_history(history):
    out = []
    for m in (history or [])[-10:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            out.append({"role": m["role"], "content": m["content"][:2000]})
    return out


def run_agent(conn, user_id, message, history=None, today=None, llm_chat=None, max_steps=8):
    llm_chat = llm_chat or llm.chat
    today = today or lusaka_today()
    tools = Tools(conn, user_id, today)
    messages = [{"role": "system", "content": system_prompt(today)}, *_clean_history(history),
                {"role": "user", "content": message[:2000]}]
    effort = os.environ.get("AGENT_REASONING_EFFORT", "low") or None
    kw = {"reasoning_effort": effort} if effort else {}
    seen, trace, reply, steps = {}, [], "", 0
    for step in range(max_steps):
        steps = step + 1
        last = step == max_steps - 1
        choice = llm_chat(messages, tools=None if last else TOOLS, max_tokens=4000, **kw)
        msg = choice["message"]
        calls = msg.get("tool_calls") or []
        if not calls or last:
            reply = (msg.get("content") or "").strip()
            break
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:  # the model may call several tools at once; every call id needs its own result
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
                key = (name, json.dumps(args, sort_keys=True))
                if key not in seen:
                    seen[key] = tools.call(name, args)
                result = seen[key]
            except json.JSONDecodeError:
                args, result = None, {"error": "arguments were not valid JSON"}
            trace.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result)})
    if not reply:
        reply = "I've lined up the options below for your approval." if tools.proposed else FALLBACK
    return {"reply": reply, "plan_id": tools.plan_id, "proposed": tools.proposed, "contacts": tools.contacts,
            "trace": trace, "steps": steps}