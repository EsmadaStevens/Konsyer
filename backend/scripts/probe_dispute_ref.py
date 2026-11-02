"""Compare the photo check WITH and WITHOUT the seller's own picture, on your real model (uses a little credit).

    python -m scripts.probe_dispute_ref customer.jpg seller_picture.jpg "French tips gel set: nude pink base, white tips"
    python -m scripts.probe_dispute_ref wrong.jpg seller_picture.jpg "listing text" --note "mine are plain red"

Prints both answers side by side after the safety checks. Nothing is stored and nothing moves money.
Cases worth trying: a correct photo, a clearly wrong one, a blurry one, and a photo of something unrelated.
"""
import argparse
import json
import pathlib

from app import dispute_reference, disputes


def _short(o):
    return {k: o[k] for k in ("verdict", "model_verdict", "confidence", "image_quality", "observed", "differences",
                              "adjusted") if k in o}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("customer_photo")
    ap.add_argument("seller_picture")
    ap.add_argument("listing_text")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    photo = pathlib.Path(a.customer_photo).read_bytes()
    ref = pathlib.Path(a.seller_picture).read_bytes()
    print("TEXT ONLY (what the app does today):")
    print(json.dumps(_short(disputes.assess(a.listing_text, a.note, photo)), indent=2, ensure_ascii=False))
    print("\nTEXT + SELLER PICTURE (experiment):")
    print(json.dumps(_short(dispute_reference.assess_with_reference(a.listing_text, a.note, photo, ref)),
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
