"""Discovery probe using Meta Model API search grounding (a Meta feature, so allowed).

Run from the backend folder with the venv active:
    python -m scripts.probe_search                      (all 8 queries: slow and uses many tokens)
    python -m scripts.probe_search --only 1,4 --debug   (queries 1 and 4, plus the response structure)
    python -m scripts.probe_search --only 1 --max-tool-calls 4
    python -m scripts.probe_search --only 1 --effort low

Needs META_API_KEY (or MODEL_API_KEY) in the environment or in the project-root .env.
Prints to the screen only; nothing is written to disk. Uses 8 search calls (a few cents at most).
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

URL = "https://api.meta.ai/v1/responses"
MODEL = "muse-spark-1.3"
QUERIES = [
    ("hair salon", "Kabulonga"),
    ("braids hairdresser", "Woodlands"),
    ("barber shop", "Roma"),
    ("nail technician", "Northmead"),
    ("makeup artist", "Lusaka"),
    ("caterer", "Lusaka"),
    ("photographer", "Lusaka"),
    ("hair salon that has a Facebook page", "Chilenje"),
]
PROMPT = (
    "Use web search to find real businesses offering '{service}' in {area}, Lusaka, Zambia. "
    "Return ONLY a JSON array (at most 6 items, no other text). Each item has these keys: "
    "name, area, phone (exactly as shown on the source, or null), whatsapp_mentioned (true or false), "
    "price_text (the exact price wording from the source, or null; never estimate), "
    "source_url (the page where you saw it), platform (website, facebook, instagram, directory or other), "
    "image_url (a direct link to an image of a price list, menu or flyer that is shown on that page, or null). "
    "Use null for anything the source does not show. Do not invent businesses, numbers or prices."
)


def load_key():
    for name in ("META_API_KEY", "MODEL_API_KEY"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    env = Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            for name in ("META_API_KEY", "MODEL_API_KEY"):
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def classify_phone(raw):
    """Plain code. (normalized +260 number or None, kind). A mobile number is not proof of WhatsApp."""
    if not raw:
        return None, "none"
    digits = re.sub(r"\D", "", str(raw))
    if digits.startswith("260"):
        national = digits[3:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        national = digits
    if len(national) != 9:
        return None, "odd"
    kind = "mobile" if national[0] in "79" else "landline" if national[0] == "2" else "other"
    return "+260" + national, kind


def collect(resp):
    """Return (text, set of cited urls, number of web_search_call items) from a Responses API body."""
    text, urls, calls = [], set(), 0
    for item in resp.get("output", []) or []:
        if item.get("type") == "web_search_call":
            calls += 1
        if item.get("type") == "message":
            for part in item.get("content", []) or []:
                if part.get("type") == "output_text":
                    text.append(part.get("text", ""))
                    for a in part.get("annotations", []) or []:
                        if a.get("type") == "url_citation" and a.get("url"):
                            urls.add(a["url"])
    return "\n".join(text), urls, calls


def urls_in(obj, found=None):
    """Every http(s) string anywhere inside a JSON value (to see whether search calls list their sources)."""
    found = set() if found is None else found
    if isinstance(obj, str):
        if obj.startswith(("http://", "https://")):
            found.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            urls_in(v, found)
    elif isinstance(obj, list):
        for v in obj:
            urls_in(v, found)
    return found


def shrink(obj, n=110):
    """Copy of a JSON value with long strings cut, so the structure is readable."""
    if isinstance(obj, str):
        return obj if len(obj) <= n else obj[:n] + "..."
    if isinstance(obj, dict):
        return {k: shrink(v, n) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shrink(v, n) for v in obj[:6]] + (["..."] if len(obj) > 6 else [])
    return obj


def parse_leads(text):
    a, b = text.find("["), text.rfind("]")
    if a < 0 or b <= a:
        return None
    try:
        data = json.loads(text[a:b + 1])
    except ValueError:
        return None
    return [x for x in data if isinstance(x, dict)]


def host(u):
    try:
        return (urlparse(u).netloc or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def request_body(service, area, args):
    body = {"model": MODEL, "input": PROMPT.format(service=service, area=area), "tools": [{"type": "web_search"}]}
    if args.max_tool_calls:
        body["max_tool_calls"] = args.max_tool_calls      # unverified for Meta: a 400 reply tells us
    if args.effort:
        body["reasoning"] = {"effort": args.effort}       # unverified for Meta: a 400 reply tells us
    return body


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma list of query numbers, e.g. 1,4")
    ap.add_argument("--debug", action="store_true", help="print the response structure of the first query")
    ap.add_argument("--max-tool-calls", type=int, default=0)
    ap.add_argument("--effort", default="", help="reasoning effort to try, e.g. low")
    args = ap.parse_args()
    picked = {int(x) for x in args.only.split(",") if x.strip().isdigit()}
    queries = [q for i, q in enumerate(QUERIES, 1) if not picked or i in picked]
    key = load_key()
    if not key:
        print("No META_API_KEY found. Add it to the root .env (never paste it in chat).")
        return
    tot = dict(leads=0, area=0, phone=0, mobile=0, price=0, cited=0, fb=0, images=0, parse_fail=0)
    first = True
    for service, area in queries:
        print(f"\n=== {service} | {area}")
        t0 = time.time()
        try:
            r = requests.post(
                URL, timeout=180,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=request_body(service, area, args))
        except requests.RequestException as e:
            print("  request failed:", e)
            continue
        print(f"  HTTP {r.status_code} in {time.time() - t0:.1f}s")
        if r.status_code != 200:
            print("  ", r.text[:400])
            continue
        body = r.json()
        text, cited, calls = collect(body)
        in_calls = urls_in([i for i in body.get("output", []) if i.get("type") == "web_search_call"])
        cited = cited | in_calls
        leads = parse_leads(text)
        usage = body.get("usage") or {}
        print(f"  search calls: {calls} | urls in annotations or search calls: {len(cited)} | "
              f"tokens in/out: {usage.get('input_tokens')}/{usage.get('output_tokens')}")
        if args.debug and first:
            for item in body.get("output", [])[:3]:
                print("  STRUCTURE:", json.dumps(shrink(item), ensure_ascii=False)[:900])
        first = False
        if leads is None:
            tot["parse_fail"] += 1
            print("  could not parse a JSON array. First 300 chars of text:\n  ", text[:300].replace("\n", " "))
            continue
        for x in leads:
            phone, kind = classify_phone(x.get("phone"))
            src = str(x.get("source_url") or "")
            in_area = area.lower() in f"{x.get('area') or ''} {x.get('name') or ''}".lower()
            is_cited = src in cited
            is_fb = "facebook.com" in host(src) or x.get("platform") == "facebook"
            tot["leads"] += 1
            tot["area"] += in_area
            tot["phone"] += phone is not None
            tot["mobile"] += kind == "mobile"
            tot["price"] += bool(x.get("price_text"))
            tot["cited"] += is_cited
            tot["fb"] += is_fb
            tot["images"] += bool(x.get("image_url"))
            print(f"  - {str(x.get('name'))[:40]:40} | area:{'Y' if in_area else 'n'} | {kind:8} | "
                  f"price:{'Y' if x.get('price_text') else 'n'} | cited:{'Y' if is_cited else 'n'}")
            print(f"      phone: {x.get('phone')}  | price_text: {x.get('price_text')}")
            print(f"      url:   {src}")
            if x.get("image_url"):
                print(f"      image: {x.get('image_url')}")
    print("\n=== SUMMARY (counts only)")
    for k, v in tot.items():
        print(f"  {k:12} {v}")
    print("  cited = the lead's source_url is one of the search citations (a code-side honesty check).")
    print("  NEXT: open 5 source URLs yourself and tell me how many phone numbers really match the page.")


if __name__ == "__main__":
    main()