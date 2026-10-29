"""Discovery probe using Meta Model API search grounding (a Meta feature, so allowed).

Run from the backend folder with the venv active:
    python -m scripts.probe_search

Needs META_API_KEY (or MODEL_API_KEY) in the environment or in the project-root .env.
Prints to the screen only; nothing is written to disk. Uses 8 search calls (a few cents at most).
"""
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
    "source_url (the page where you saw it), platform (website, facebook, instagram, directory or other). "
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


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    key = load_key()
    if not key:
        print("No META_API_KEY found. Add it to the root .env (never paste it in chat).")
        return
    tot = dict(leads=0, area=0, phone=0, mobile=0, price=0, cited=0, fb=0, parse_fail=0)
    for service, area in QUERIES:
        print(f"\n=== {service} | {area}")
        t0 = time.time()
        try:
            r = requests.post(
                URL, timeout=180,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": MODEL, "input": PROMPT.format(service=service, area=area),
                      "tools": [{"type": "web_search"}]})
        except requests.RequestException as e:
            print("  request failed:", e)
            continue
        print(f"  HTTP {r.status_code} in {time.time() - t0:.1f}s")
        if r.status_code != 200:
            print("  ", r.text[:400])
            continue
        body = r.json()
        text, cited, calls = collect(body)
        leads = parse_leads(text)
        print(f"  search calls: {calls} | cited urls: {len(cited)} | usage: {body.get('usage')}")
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
            print(f"  - {str(x.get('name'))[:34]:34} | area:{'Y' if in_area else 'n'} | {kind:8} | "
                  f"price:{'Y' if x.get('price_text') else 'n'} | cited:{'Y' if is_cited else 'n'} | {host(src)}")
    print("\n=== SUMMARY (counts only)")
    for k, v in tot.items():
        print(f"  {k:12} {v}")
    print("  cited = the lead's source_url is one of the search citations (a code-side honesty check).")
    print("  NEXT: open 5 source URLs yourself and tell me how many phone numbers really match the page.")


if __name__ == "__main__":
    main()