"""Can the Meta Model API read an image given only its public URL? Tries both API shapes from Meta's docs.
Prints status codes and a short reply. It never prints your key.

Run from the backend folder with the venv active:
    python -m scripts.probe_image https://example.com/some-price-list.jpg
    python -m scripts.probe_image <url> --model muse-spark-1.2
Use a PUBLIC https image URL (for example one printed as 'image:' by probe_search, or any public flyer image).
"""
import argparse
import re
import sys
import time

import requests

from scripts.probe_search import collect, load_key

BASE = "https://api.meta.ai/v1"
ASK = ("Read this image. If it is a price list, menu or flyer, return ONLY a JSON array of objects with keys "
       "item and price_text (exact wording, never estimate). If it is not one, say in one sentence what it is.")


def short(text, n=600):
    return re.sub(r"\s+", " ", text or "")[:n]


def run(label, path, key, body, pick):
    t0 = time.time()
    try:
        r = requests.post(BASE + path, timeout=180, json=body,
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    except requests.RequestException as e:
        print(f"\n[{label}] POST {path}\n  request failed: {e}")
        return
    print(f"\n[{label}] POST {path}\n  HTTP {r.status_code} in {time.time() - t0:.1f}s")
    if r.status_code != 200:
        print("  body:", short(r.text, 400))
        return
    try:
        print("  reply:", short(pick(r.json())))
    except (ValueError, KeyError, IndexError, TypeError):
        print("  200 but unexpected shape:", short(r.text, 300))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--model", default="muse-spark-1.3")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not a.url.startswith("https://"):
        print("Give a public https:// image URL.")
        return
    key = load_key()
    if not key:
        print("No META_API_KEY found in the environment or the root .env.")
        return
    print("image:", a.url[:120], "| model:", a.model)
    run("1 responses + input_image", "/responses", key, {
        "model": a.model,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": ASK}, {"type": "input_image", "image_url": a.url}]}]},
        lambda j: collect(j)[0])
    run("2 chat completions + image_url", "/chat/completions", key, {
        "model": a.model, "max_tokens": 4000,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": ASK}, {"type": "image_url", "image_url": {"url": a.url}}]}]},
        lambda j: j["choices"][0]["message"]["content"])
    print("\nPaste this whole output (it contains no key).")


if __name__ == "__main__":
    main()