"""Flyer reading. The model READS (extraction.py); the business owner reviews the draft in the listing form; plain code
validates and writes through merchants.add_listing. Nothing here creates a listing, and only a registered business can
reach it (see listing_drafts.py), so nobody can post someone else's flyer with their own phone number.
The uploaded image goes to a temp file for the model call and is deleted straight after. It is never stored."""
import logging
import os
import re
import tempfile
from datetime import timedelta

from .extraction import extract_flyer

log = logging.getLogger("konsyer.flyers")

MAX_BYTES = 10 * 1024 * 1024
MAX_PRICE = 5_000_000          # K50,000 in ngwee
PLACEHOLDER_CAPACITY = 100     # a flyer does not print capacity; this is a placeholder, not a real number
KINDS = ("flyer", "screenshot")
TYPES = ("event", "activity", "dining", "service")
STATUS = {"bad_image": 415, "too_big": 413, "not_found": 404, "forbidden": 403, "invalid": 422,
          "extract_failed": 502, "conflict": 409}
_UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
_CAT = timedelta(hours=2)      # Lusaka is UTC+2, no daylight saving


class FlyerError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def detect_image(data: bytes):
    """File type from the first bytes, not from the filename. Returns an extension or None."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def normalize_phone(text):
    """Digits only, Zambian local format 0XXXXXXXXX -> 260XXXXXXXXX (the form wa.me links need)."""
    if not text or not str(text).strip():
        return None
    digits = re.sub(r"\D", "", str(text))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        digits = "260" + digits[1:]
    if not 9 <= len(digits) <= 15:
        raise ValueError("phone")
    return digits


def read_flyer(data, today):
    """Validate the upload, run the vision model on a temp file, return the cleaned draft dict."""
    if not data:
        raise FlyerError("bad_image", "The file is empty.")
    if len(data) > MAX_BYTES:
        raise FlyerError("too_big", f"That image is too large (the limit is {MAX_BYTES // (1024 * 1024)} MB).")
    ext = detect_image(data)
    if ext is None:
        raise FlyerError("bad_image", "Please upload a JPEG, PNG or WebP image.")
    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return extract_flyer(path, today)
    except Exception as e:
        log.exception("flyer extraction failed")
        raise FlyerError("extract_failed", "Could not read that image. Try a clearer photo.") from e
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
