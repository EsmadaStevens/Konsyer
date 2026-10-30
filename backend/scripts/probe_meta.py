"""Find out which Meta Model API endpoints and model ids your key can use. Prints status codes and short
error text only. It NEVER prints the key (only its length and whether it has stray characters).

Run from the backend folder with the venv active:
    python -m scripts.probe_meta
    python -m scripts.probe_meta --model muse-spark-1.2     (try another model id)
"""
import argparse
import re
import sys
import time

import requests

from scripts.probe_search import collect, load_key

BASE = "https://api.meta.ai/v1"


def short(text, n=300):
    return re.sub(r"\s+", " ", text or "")[:n]


def call(label, method, path, key, body=None):
    url = BASE + path
    t0 = time.time()
    try:
        r = requests.request(method, url, timeout=120, json=body,
                             headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    except requests.RequestException as e:
        print(f"\n[{label}] {method} {path}\n  request failed: {e}")
        return None
    print(f"\n[{label}] {method} {path}\n  HTTP {r.status_code} in {time.time() - t0:.1f}s")
    if r.status_code != 200:
        print("  body:", short(r.text))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="muse-spark-1.3")
    model = ap.parse_args().model
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    key = load_key()
    if not key:
        print("No META_API_KEY found in the environment or the root .env.")
        return
    has_space = re.search(r"\s", key) is not None
    has_quote = ('"' in key) or ("'" in key)
    print(f"key found: length {len(key)}, whitespace inside: {has_space}, quotes inside: {has_quote}")
    print(f"model: {model}")

    r = call("1 list models", "GET", "/models", key)
    if r is not None and r.status_code == 200:
        try:
            ids = [m.get("id") for m in r.json().get("data", [])]
            print("  model ids:", ", ".join(str(i) for i in ids[:30]) or "(none listed)")
        except ValueError:
            print("  could not parse the list")

    r = call("2 chat completions", "POST", "/chat/completions", key,
             {"model": model, "messages": [{"role": "user", "content": "Say hello in five words."}], "max_tokens": 500})
    if r is not None and r.status_code == 200:
        try:
            print("  reply:", short(r.json()["choices"][0]["message"].get("content"), 120))
        except (ValueError, KeyError, IndexError):
            print("  200 but unexpected shape:", short(r.text))

    r = call("3 responses, no tools", "POST", "/responses", key, {"model": model, "input": "Say hello in five words."})
    if r is not None and r.status_code == 200:
        print("  reply:", short(collect(r.json())[0], 120))

    r = call("4 responses + web_search", "POST", "/responses", key,
             {"model": model, "input": "What is the capital of Zambia? Use web search.", "tools": [{"type": "web_search"}]})
    if r is not None and r.status_code == 200:
        text, urls, calls = collect(r.json())
        print(f"  search calls: {calls}, cited urls: {len(urls)}")
        print("  reply:", short(text, 160))
    print("\nPaste this whole output (it contains no key).")


if __name__ == "__main__":
    main()