"""Try dispute vision with your REAL model on a photo and a listing description (uses a little credit).

    python -m scripts.probe_dispute "images\\disputes\\french_tips.jpg" "French tips gel set: nude pink base with white tips"
    python -m scripts.probe_dispute photo.jpg "listing text" --note "mine are plain red"

Prints the verdict after the safety checks (poor photo or low confidence becomes 'unclear'). The photo is read from
disk, sent to the model and never stored. Nothing here moves money.
"""
import argparse
import json
import pathlib

from app import disputes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("photo")
    ap.add_argument("listing_text")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    out = disputes.assess(a.listing_text, a.note, pathlib.Path(a.photo).read_bytes())
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
