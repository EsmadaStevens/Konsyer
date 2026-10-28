"""Probe the model endpoint. Saves RAW responses to eval/probe_out/<name>.json.

Run from the backend/ folder:
  pip install -r requirements.txt
  python -m scripts.probe                      # text, tools, json
  python -m scripts.probe --image flyer.jpg    # adds image test
  python -m scripts.probe --audio note.wav     # adds audio test (experimental)
  python -m scripts.probe --only tools,tool_roundtrip
The API key is never printed or saved.
"""
import argparse, base64, json, mimetypes, pathlib, sys, time
import httpx
from app.config import llm_config

OUT = pathlib.Path(__file__).resolve().parents[2] / "eval" / "probe_out"
OUT.mkdir(parents=True, exist_ok=True)
cfg = llm_config()
MAX_TOKENS = 4000  # reasoning tokens count against this; small values leave content empty

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_listings",
        "description": "Search bookable listings (events, activities, dining, services) in Lusaka.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_price_ngwee": {"type": "integer", "description": "Max price per person in ngwee (K1 = 100 ngwee)"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    },
}

FLYER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "date": {"type": ["string", "null"], "description": "YYYY-MM-DD or null"},
        "start_time": {"type": ["string", "null"]},
        "venue": {"type": ["string", "null"]},
        "price_kwacha": {"type": ["number", "null"]},
        "serves_alcohol": {"type": ["boolean", "null"]},
        "confidence": {"type": "number"},
    },
    "required": ["title", "date", "start_time", "venue", "price_kwacha", "serves_alcohol", "confidence"],
    "additionalProperties": False,
}


def post(name, body):
    body = {"model": cfg.model, **body}
    t0 = time.time()
    rec = {"name": name, "request": body_for_log(body)}
    try:
        r = httpx.post(
            f"{cfg.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
            json=body, timeout=180,
        )
        rec["status"] = r.status_code
        try:
            rec["response"] = r.json()
        except Exception:
            rec["response_text"] = r.text[:4000]
    except Exception as e:
        rec["status"] = None
        rec["error"] = repr(e)
    rec["seconds"] = round(time.time() - t0, 2)
    (OUT / f"{name}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False))
    summarize(rec)
    return rec


def body_for_log(body):
    """Strip big base64 payloads from the saved request."""
    s = json.dumps(body)
    if len(s) < 20000:
        return body
    return json.loads(json.dumps(body).replace(
        next((p for p in _b64s(body)), "\0"), "<base64 omitted>")) if _b64s(body) else body


def _b64s(body):
    out = []
    for m in body.get("messages", []):
        c = m.get("content")
        if isinstance(c, list):
            for part in c:
                if part.get("type") == "image_url":
                    out.append(part["image_url"]["url"])
                if part.get("type") == "input_audio":
                    out.append(part["input_audio"]["data"])
    return out


def summarize(rec):
    print(f"\n=== {rec['name']} | status={rec.get('status')} | {rec['seconds']}s")
    if rec.get("error"):
        print("ERROR:", rec["error"]); return
    resp = rec.get("response")
    if not resp:
        print("BODY:", rec.get("response_text")); return
    if "choices" not in resp:
        print("NO choices; keys:", list(resp.keys()), "\n", json.dumps(resp)[:600]); return
    ch = resp["choices"][0]
    msg = ch["message"]
    print("finish_reason:", ch.get("finish_reason"))
    print("message keys:", list(msg.keys()))
    print("content:", (msg.get("content") or "")[:300].replace("\n", " "))
    if msg.get("tool_calls"):
        print("tool_calls:", json.dumps(msg["tool_calls"])[:500])
    for k in ("reasoning", "reasoning_content"):
        if msg.get(k):
            print(f"{k} present ({len(str(msg[k]))} chars)")
    print("usage:", resp.get("usage"))


def data_url(path):
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(pathlib.Path(path).read_bytes()).decode()


def t_text():
    post("text", {"messages": [{"role": "user", "content": "In one sentence, what is Lusaka known for?"}], "max_tokens": MAX_TOKENS})


def t_tools():
    return post("tools", {
        "messages": [
            {"role": "system", "content": "You are a Lusaka outings concierge. Use tools to find listings; never guess. Today is Tuesday 2026-10-27 (timezone Africa/Lusaka)."},
            {"role": "user", "content": "Saturday, 5 of us, K2,000 total, something active. What's on?"},
        ],
        "tools": [SEARCH_TOOL], "tool_choice": "auto", "max_tokens": MAX_TOKENS,
    })


def t_tool_roundtrip():
    first = json.loads((OUT / "tools.json").read_text()) if (OUT / "tools.json").exists() else None
    if not first or "response" not in first or "choices" not in first["response"]:
        first = t_tools()
    msg = first.get("response", {}).get("choices", [{}])[0].get("message", {})
    calls = msg.get("tool_calls")
    if not calls:
        print("\n=== tool_roundtrip skipped: model made no tool call in 'tools' test."); return
    fake = {"results": [{"id": "L1", "name": "Sample: Go-karting", "price_ngwee": 15000, "sample_data": True}]}
    assistant = {"role": "assistant", "content": msg.get("content"), "tool_calls": calls}
    messages = first["request"]["messages"] + [assistant] + [
        {"role": "tool", "tool_call_id": c["id"], "content": json.dumps(fake)} for c in calls]
    post("tool_roundtrip", {"messages": messages, "tools": [SEARCH_TOOL], "max_tokens": MAX_TOKENS})


def t_low_effort():
    post("tools_low_effort", {
        "messages": [
            {"role": "system", "content": "You are a Lusaka outings concierge. Use tools to find listings; never guess. Today is Tuesday 2026-10-27 (timezone Africa/Lusaka)."},
            {"role": "user", "content": "Saturday, 5 of us, K2,000 total, something active. What's on?"},
        ],
        "tools": [SEARCH_TOOL], "tool_choice": "auto", "max_tokens": MAX_TOKENS,
        "reasoning_effort": "low",
    })


def t_json_schema():
    post("json_schema", {
        "messages": [{"role": "user", "content":
            "Today is 2026-10-27. Extract fields. Dates must be YYYY-MM-DD and times HH:MM (24h). Text: 'AFROBEATS NIGHT, Sat 1 Nov, doors 19:00, Arcades Lusaka, K150 at the door, cash bar'. Use null if unknown."}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "flyer", "schema": FLYER_SCHEMA, "strict": True}},
        "max_tokens": MAX_TOKENS,
    })


def t_json_object():
    post("json_object", {
        "messages": [{"role": "user", "content":
            "Return ONLY a JSON object with keys title, date, venue for: 'Quiz Night, 2 Nov 2026, Hot Spot Bar'."}],
        "response_format": {"type": "json_object"}, "max_tokens": MAX_TOKENS,
    })


def t_image(path):
    post("image", {
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "This is an event flyer. Return ONLY JSON with keys: title, date, start_time, venue, price_kwacha, serves_alcohol, confidence (0-1). Use null when not visible. Do not guess."},
            {"type": "image_url", "image_url": {"url": data_url(path)}},
        ]}], "max_tokens": MAX_TOKENS,
    })


def t_audio(path):
    p = pathlib.Path(path)
    fmt = p.suffix.lstrip(".").lower() or "wav"
    post("audio", {
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Transcribe this voice note exactly, then say which language it is."},
            {"type": "input_audio", "input_audio": {"data": base64.b64encode(p.read_bytes()).decode(), "format": fmt}},
        ]}], "max_tokens": MAX_TOKENS,
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image"); ap.add_argument("--audio"); ap.add_argument("--only")
    a = ap.parse_args()
    print(f"endpoint={cfg.base_url} model={cfg.model}")
    plan = {"text": t_text, "tools": t_tools, "tool_roundtrip": t_tool_roundtrip,
            "low_effort": t_low_effort, "json_schema": t_json_schema, "json_object": t_json_object}
    if a.image: plan["image"] = lambda: t_image(a.image)
    if a.audio: plan["audio"] = lambda: t_audio(a.audio)
    names = a.only.split(",") if a.only else list(plan)
    for n in names:
        if n not in plan:
            print(f"unknown/unavailable test: {n}"); continue
        plan[n]()
    print(f"\nRaw files in {OUT}")


if __name__ == "__main__":
    main()