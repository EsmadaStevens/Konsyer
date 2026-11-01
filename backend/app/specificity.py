"""Is a listing description specific enough for an automatic photo check?

The description is the seller's promise: the model compares a customer's photo with it. A vague promise ("nice nails,
call us") gives the model nothing to compare, so such a listing is NOT photo-checked: a dispute goes straight to a
person. Plain code decides this; the model never does."""
import re

MIN_WORDS = 8
MIN_CHARS = 40


def is_specific(description) -> bool:
    text = " ".join(str(description or "").split())
    return len(text) >= MIN_CHARS and len(re.findall(r"[A-Za-z0-9']+", text)) >= MIN_WORDS


ADVICE = ("Add details customers can check: colour, shape or size, material, finish. Listings with a specific "
          "description get an automatic photo check if a customer reports a problem; vague ones go straight to a person.")
