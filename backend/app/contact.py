"""Contact helpers. Plain code, no model. Nothing here sends anything: it only builds a message and links
that the USER taps (WhatsApp, Messenger, Instagram, a phone call, or the source page)."""
import re
from urllib.parse import quote, urlparse

_URL = re.compile(r"(https?://|www\.)\S+", re.I)
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")
_FB_HOSTS = {"facebook.com", "m.facebook.com", "web.facebook.com", "mbasic.facebook.com"}
_FB_RESERVED = {"profile.php", "groups", "pages", "people", "watch", "share", "sharer", "p", "permalink.php",
                "events", "marketplace", "photo", "photo.php", "story.php", "login", "l.php", "hashtag",
                "reel", "reels", "stories", "dialog", "plugins", "policies", "help", "business", "public"}
_FB_SUBPAGES = {"about", "posts", "photos", "reviews", "services", "videos", "reels"}
_FB_NAME = re.compile(r"^[A-Za-z0-9.\-]{5,80}$")
_IG_RESERVED = {"p", "reel", "reels", "explore", "stories", "accounts", "tv", "direct", "about"}
_IG_NAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def one_line(text, n):
    """Strip control characters and newlines, collapse spaces, cap the length."""
    return re.sub(r"\s+", " ", _CTRL.sub(" ", str(text or ""))).strip()[:n]


def clean_details(text, n=200):
    """What the user wants, with links removed so the model cannot smuggle a URL into a message."""
    return one_line(_URL.sub("", str(text or "")), n)


def parse_zm_phone(raw):
    """Return '260XXXXXXXXX' for a plausible Zambian number (mobile 7/9, landline 2), else None."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if digits.startswith("00"):
        digits = digits[2:]
    national = digits[3:] if digits.startswith("260") else digits[1:] if digits.startswith("0") else digits
    if len(national) != 9 or national[0] not in "279":
        return None
    return "260" + national


def phone_kind(digits):
    if not digits:
        return None
    return "mobile" if digits[3] in "79" else "landline"


def host_of(url):
    try:
        return (urlparse(url).netloc or "").lower().split(":")[0].removeprefix("www.")
    except ValueError:
        return ""


def is_http_url(url):
    try:
        p = urlparse(str(url))
    except ValueError:
        return False
    return p.scheme in ("http", "https") and "." in p.netloc and len(str(url)) <= 300 and " " not in str(url)


def platform_of(url):
    h = host_of(url)
    if h in _FB_HOSTS or h == "fb.com":
        return "facebook"
    if h == "instagram.com":
        return "instagram"
    return "website"


def build_message(name, details):
    what = clean_details(details) or "your services"
    return (f"Hello {one_line(name, 60)}, I found you through Konsyer, a Lusaka booking assistant. "
            f"I'm looking for: {what}. Could you share your price and availability? Thank you.")


def whatsapp_link(digits, text):
    return f"https://wa.me/{digits}?text={quote(text)}"


def messenger_link(url):
    """https://m.me/<page> for a plain Facebook page URL, else None. No profile ids, groups or posts."""
    if platform_of(url) != "facebook":
        return None
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts or parts[0].lower() in _FB_RESERVED or not _FB_NAME.match(parts[0]):
        return None
    if len(parts) > 2 or (len(parts) == 2 and parts[1].lower() not in _FB_SUBPAGES):
        return None
    return f"https://m.me/{parts[0]}"


def instagram_link(url):
    if platform_of(url) != "instagram":
        return None
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts or parts[0].lower() in _IG_RESERVED or not _IG_NAME.match(parts[0]):
        return None
    return f"https://www.instagram.com/{parts[0]}/"


def channels(phone, source_url, message):
    """Ordered list of {kind, label, url}. The UI shows the message with a Copy button next to these."""
    out = []
    if phone and phone_kind(phone) == "mobile":
        out.append({"kind": "whatsapp", "label": "Open WhatsApp", "url": whatsapp_link(phone, message)})
    if source_url and is_http_url(source_url):
        m, ig = messenger_link(source_url), instagram_link(source_url)
        if m:
            out.append({"kind": "messenger", "label": "Open Messenger (paste the message)", "url": m})
        if ig:
            out.append({"kind": "instagram", "label": "Open Instagram (paste the message)", "url": ig})
    if phone:
        out.append({"kind": "call", "label": "Call", "url": f"tel:+{phone}"})
    if source_url and is_http_url(source_url):
        out.append({"kind": "page", "label": "Open the source page", "url": source_url})
    return out