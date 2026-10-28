"""Flyer/screenshot -> draft listing. The model READS; plain code NORMALIZES and flags doubts."""
import base64
import json
import mimetypes
import pathlib
import re
from datetime import date
from typing import Optional

from .llm import chat, LLMError
from .money import kwacha_to_ngwee

_NS = lambda t: {"type": [t, "null"]}
FLYER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "listing_type": {"type": "string", "enum": ["event", "activity", "dining", "service"]},
        "availability_text": {**_NS("string"), "description": "Days/hours offered, exactly as printed (e.g. Monday - Thursday)"},
        "date_text": {**_NS("string"), "description": "Date exactly as printed"},
        "start_time_text": {**_NS("string"), "description": "Start or doors time exactly as printed"},
        "venue": _NS("string"),
        "prices": {"type": "array", "items": {
            "type": "object",
            "properties": {"label": _NS("string"), "kwacha": _NS("number")},
            "required": ["label", "kwacha"], "additionalProperties": False}},
        "performers": {"type": "array", "items": {"type": "string"},
                       "description": "Artists, DJs, MCs named on the image; empty list if none"},
        "serves_alcohol": _NS("boolean"),
        "contact_phone": _NS("string"),
        "confidence": {"type": "number"},
    },
    "required": ["title", "listing_type", "availability_text", "date_text", "start_time_text", "venue", "prices",
                 "performers", "serves_alcohol", "contact_phone", "confidence"],
    "additionalProperties": False,
}

PROMPT = (
    "Read this event flyer or screenshot. Copy dates and times EXACTLY as printed; do not convert or guess. "
    "List every ticket price or tier with its label (amounts in kwacha). Use null when something is not "
    "visible. serves_alcohol is true only if the image says drinks or a bar are available. "
    "listing_type: event (one-off show/party), activity (things to do), dining, or service (salon, repairs, price lists). "
    "For services, put opening days or hours in availability_text. "
    "performers: only names printed on the image, empty list if none. contact_phone: the booking, call or WhatsApp number. "
    "confidence is 0 to 1 for how sure you are overall."
)

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun",
                                       "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_WEEKDAYS = {d: i for i, d in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])}
_MON_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"


def parse_date(text: Optional[str], today: date) -> tuple[Optional[date], str]:
    """Returns (date, status). status: ok | assumed_year | weekday_mismatch | unparsed.
    A printed weekday is used to confirm the year; a mismatch is flagged, never silently 'fixed'."""
    if not text:
        return None, "unparsed"
    t = text.lower()
    wd = re.search(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*", t)
    weekday = _WEEKDAYS[wd[1]] if wd else None
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", t)
    if iso:
        try:
            return date(int(iso[1]), int(iso[2]), int(iso[3])), "ok"
        except ValueError:
            return None, "unparsed"
    num = re.search(r"\b(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})\b", t)
    if num:  # Zambia writes day first: 18.09.26 = 18 Sep 2026
        day, month, y = int(num[1]), int(num[2]), int(num[3])
        y = y + 2000 if y < 100 else y
        try:
            d = date(y, month, day)
        except ValueError:
            return None, "unparsed"
        if weekday is not None and d.weekday() != weekday:
            return d, "weekday_mismatch"
        return d, ("assumed_day_first" if day <= 12 and month <= 12 and day != month else "ok")
    m1 = re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:of\s+)?" + _MON_RE + r"\b", t)
    m2 = re.search(r"\b" + _MON_RE + r"\s*(\d{1,2})\b", t)
    if m1:
        day, month = int(m1[1]), _MONTHS[m1[2]]
    elif m2:
        day, month = int(m2[2]), _MONTHS[m2[1]]
    else:
        return None, "unparsed"
    yr = re.search(r"\b(20\d{2})\b", t)

    def build(y):
        try:
            return date(y, month, day)
        except ValueError:
            return None

    if yr:
        d = build(int(yr[1]))
        if d is None:
            return None, "unparsed"
        return d, ("weekday_mismatch" if weekday is not None and d.weekday() != weekday else "ok")
    cands = [d for d in (build(today.year - 1), build(today.year), build(today.year + 1)) if d]
    eligible = [d for d in cands if -400 <= (d - today).days <= 400 and (today - d).days <= 120]
    if weekday is not None:
        match = [d for d in eligible if d.weekday() == weekday]
        if match:
            return min(match, key=lambda d: abs((d - today).days)), "ok"
        fallback = build(today.year)
        return fallback, "weekday_mismatch" if fallback else "unparsed"
    if eligible:
        return min(eligible, key=lambda d: abs((d - today).days)), "assumed_year"
    fallback = build(today.year)
    return fallback, "assumed_year" if fallback else "unparsed"


def parse_time(text: Optional[str]) -> Optional[str]:
    """Returns 'HH:MM' (24h) or None."""
    if not text:
        return None
    t = text.lower()
    m = re.search(r"\b([01]?\d|2[0-3])\s*[:h.]\s*([0-5]\d)\s*(am|pm)?(?!\d)", t)
    if m:
        h, mi, ap = int(m[1]), int(m[2]), m[3]
    else:
        m = re.search(r"\b(1[0-2]|0?[1-9])\s*(am|pm)\b", t)
        if not m:
            return None
        h, mi, ap = int(m[1]), 0, m[2]
    if ap:
        if h > 12:
            return None
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
    return f"{h:02d}:{mi:02d}"


def normalize_prices(prices) -> list[dict]:
    """Accepts [{'label','kwacha'}] or a bare list of numbers. Returns [{'label','amount'}] in ngwee."""
    out = []
    for p in prices or []:
        label, val = (p.get("label"), p.get("kwacha")) if isinstance(p, dict) else (None, p)
        if isinstance(val, str):
            digits = re.sub(r"[^\d.]", "", val)
            val = float(digits) if digits else None
        if isinstance(val, (int, float)) and val > 0:
            out.append({"label": label, "amount": kwacha_to_ngwee(val)})
    return out


MAX_PLAUSIBLE_PRICE = 1_000_000  # K10,000 in ngwee. Prices above this get flagged as possible misreads.
_BAD_DATE = ("weekday_mismatch", "unparsed", "assumed_day_first")


def clean_draft(raw: dict, today: date) -> dict:
    ltype = raw.get("listing_type") or "event"
    if ltype not in ("event", "activity", "dining", "service"):
        ltype = "event"
    d, dstatus = parse_date(raw.get("date_text"), today)
    prices = normalize_prices(raw.get("prices"))
    draft = {
        "title": (raw.get("title") or "").strip(),
        "listing_type": ltype,
        "availability_text": raw.get("availability_text"),
        "date": d.isoformat() if d else None,
        "date_text": raw.get("date_text"),
        "date_status": dstatus,
        "start_time": parse_time(raw.get("start_time_text")),
        "venue": raw.get("venue"),
        "performers": [p.strip() for p in (raw.get("performers") or []) if isinstance(p, str) and p.strip()],
        "prices": prices,
        "price_min": min((p["amount"] for p in prices), default=None),
        "serves_alcohol": raw.get("serves_alcohol"),
        "contact_phone": raw.get("contact_phone"),
        "model_confidence": raw.get("confidence"),
    }
    need = []
    if not draft["title"]:
        need.append("title")
    if ltype == "service":
        if not (draft["contact_phone"] or draft["venue"]):
            need.append("contact_or_location")
    else:
        if draft["date"] is None or dstatus in _BAD_DATE:
            need.append("date")
        if draft["start_time"] is None:
            need.append("start_time")
        if not draft["venue"]:
            need.append("venue")
    if not prices or max(p["amount"] for p in prices) > MAX_PLAUSIBLE_PRICE:
        need.append("price")
    draft["needs_confirmation"] = need
    return draft


def _data_url(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(pathlib.Path(path).read_bytes()).decode()


def extract_flyer(image_path: str, today: Optional[date] = None) -> dict:
    today = today or date.today()
    choice = chat(
        [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": _data_url(image_path)}}]}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "flyer", "schema": FLYER_SCHEMA, "strict": True}},
        max_tokens=8000,
    )
    content = (choice["message"].get("content") or "").strip()
    if not content:
        raise LLMError(f"empty answer (finish_reason={choice.get('finish_reason')}); raise max_tokens?")
    content = re.sub(r"^```(?:json)?|```$", "", content).strip()
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"model returned invalid JSON: {e}")
    return clean_draft(raw, today)