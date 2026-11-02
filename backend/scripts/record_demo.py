"""Record the model's answers for the keyless demo. Run this ONCE, with your keys, on your own machine.

    python -m scripts.record_demo --out demo/trace.jsonl
    python -m scripts.record_demo --out demo/trace.jsonl --skip-chat --dispute-photo "demo/images/pink_tips_remote.jpg"
    python -m scripts.record_demo --out demo/trace.jsonl ^
        --dispute-photo "demo/images/red_nails.jpg" --dispute-photo "demo/images/french_tips.jpg" ^
        --product-photo "demo/images/braids.jpg" --flyer "demo/images/flyer.jpg" --extra "a cheap lunch for two"

It runs a fixed set of requests against a fresh, temporary database with the built-in sample data and the SAMPLE web
leads (never real businesses), calls your live model for each, and appends the answers to the trace file.
Then anyone can run the app without a key:   set KONSYER_REPLAY=demo/trace.jsonl

Photos are never written to the trace, only a hash of each. Recordings are matched by FILE NAME, so anyone replaying must upload files with the same names, so
commit the images you recorded with under demo/images/. Open the trace in a text editor before you commit it:
every line has a readable 'label' and the model's answer.
"""
import argparse
import os
import pathlib
import sys
import tempfile

SCENARIOS = [
    "Saturday, 5 of us, K2,000, something active",
    "I want French tip nails. My budget is K300",
    "Book the press-on nails for Saturday for one person. Budget K200",
    "Find me a hair salon in Lusaka",
    "Plan a dinner for 4 people, my budget is K50",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--extra", action="append", default=[], help="another request to record (repeatable)")
    ap.add_argument("--dispute-photo", action="append", default=[], help="a customer photo to check (repeatable)")
    ap.add_argument("--listing-text", default=None, help="listing text for the dispute photos (default: press-on nails)")
    ap.add_argument("--product-photo", action="append", default=[], help="a product photo to draft a listing from")
    ap.add_argument("--flyer", action="append", default=[], help="a flyer to read")
    ap.add_argument("--skip-chat", action="store_true",
                    help="record only the photos given (use this to ADD pictures to an existing trace without "
                         "recording the chat examples a second time)")
    a = ap.parse_args()

    os.environ["DISCOVERY_PROVIDER"] = "mock"          # sample leads only: nothing real in the recording
    os.environ["KONSYER_DB"] = str(pathlib.Path(tempfile.mkdtemp()) / "record.db")
    os.environ.pop("KONSYER_REPLAY", None)
    os.environ.pop("KONSYER_STUB", None)
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    os.environ["KONSYER_RECORD"] = str(out)

    from app import disputes, flyers, listing_drafts, llm, merchants
    from app.agent.loop import run_agent
    from app.db import get_conn, init_db
    from app.seed import seed
    from app.timeutil import lusaka_today

    if not os.environ.get("LLM_API_KEY"):
        from app import config  # noqa: F401  (loads .env)
    if not os.environ.get("LLM_API_KEY"):
        sys.exit("LLM_API_KEY is missing. Put it in .env first: recording needs the live model.")

    today = lusaka_today()
    conn = get_conn(os.environ["KONSYER_DB"])
    init_db(conn)
    seed(conn, today)
    conn.execute("INSERT OR IGNORE INTO users(id, name) VALUES (2, 'Recorder')")
    conn.commit()

    done = failed = 0
    for text in ([] if a.skip_chat else SCENARIOS) + a.extra:
        try:
            r = run_agent(conn, 1, text, [])
            print(f"ok   {text!r}\n     -> {r['reply'][:110]!r}")
            done += 1
        except Exception as e:
            print(f"FAIL {text!r}: {type(e).__name__}: {e}")
            failed += 1

    listing = a.listing_text or ("Press-on nail set, 24 pieces: nude pink base with white French tips, almond shape, "
                                 "glossy finish. Delivered in the window shown.")
    for path in a.dispute_photo:
        try:
            with llm.input_name(path):
                res = disputes.assess(f"Sample Press-On Nails. {listing}", "", pathlib.Path(path).read_bytes())
            print(f"ok   dispute photo {path}: {res['verdict']} ({res['confidence']})")
            done += 1
        except Exception as e:
            print(f"FAIL dispute photo {path}: {e}")
            failed += 1

    mid = merchants.register(conn, 2, {"name": "Recorder", "area": "Kabulonga"})["merchant_id"]
    for path in a.product_photo:
        try:
            with llm.input_name(path):
                res = listing_drafts.from_photo(conn, 2, mid, pathlib.Path(path).read_bytes())
            print(f"ok   product photo {path}: {res['title']!r}")
            done += 1
        except Exception as e:
            print(f"FAIL product photo {path}: {e}")
            failed += 1
    for path in a.flyer:
        try:
            with llm.input_name(path):
                res = flyers.read_flyer(pathlib.Path(path).read_bytes(), today)
            print(f"ok   flyer {path}: {str(res.get('title') or res)[:60]!r}")
            done += 1
        except Exception as e:
            print(f"FAIL flyer {path}: {e}")
            failed += 1

    print(f"\nRecorded {done} request(s), {failed} failed. Trace: {out} ({out.stat().st_size // 1024 if out.exists() else 0} KB)")
    print("Open it in a text editor and read the 'label' on each line before you commit it.")


if __name__ == "__main__":
    main()
