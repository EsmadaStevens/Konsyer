"""Discovery of services nobody has registered on Konsyer. Plain code around a provider.

A provider returns RAW leads (it may be a Meta model with web search grounding, which can be wrong).
Everything is re-validated here in plain code. Leads are unverified, never bookable, never payable, and are
kept only for the current request (nothing a provider returns is stored). Prices stay as the seller's own
wording (price_text) and are never turned into money."""
import os
from typing import Protocol

from .contact import (host_of, is_http_url, one_line, parse_zm_phone, phone_kind, platform_of)

MAX_LEADS = 6


class DiscoveryProvider(Protocol):
    name: str
    is_sample: bool

    def search(self, service: str, area: str) -> list: ...


class MockDiscovery:
    """Fictional results so the repo runs with no key. Every name starts with 'Sample'. If KONSYER_DEMO_LEAD_PHONE
    is set (use YOUR OWN number), the first lead gets it so the WhatsApp flow can be demoed safely."""
    name, is_sample = "mock", True

    def search(self, service, area):
        s = one_line(service, 40).title() or "Service"
        return [
            {"name": f"Sample {s} Studio", "area": area, "phone": os.environ.get("KONSYER_DEMO_LEAD_PHONE") or None,
             "whatsapp_mentioned": True, "price_text": "from K250 (sample)", "source_url": "https://example.com/sample-studio"},
            {"name": f"Sample {s} Lounge", "area": area, "phone": None, "whatsapp_mentioned": False,
             "price_text": None, "source_url": "https://www.facebook.com/konsyer.sample.lounge"},
            {"name": f"Sample {s} by Konsyer", "area": area, "phone": None, "whatsapp_mentioned": False,
             "price_text": None, "source_url": "https://www.instagram.com/konsyer.sample.beauty"},
        ]


def get_provider():
    """DISCOVERY_PROVIDER: 'mock' (default), 'none' (turn discovery off). 'meta' is added when the key works."""
    name = os.environ.get("DISCOVERY_PROVIDER", "mock").strip().lower() or "mock"
    if name == "mock":
        return MockDiscovery()
    if name == "none":
        return None
    raise ValueError(f"unknown DISCOVERY_PROVIDER {name!r}")


def clean_leads(raw, cited_urls=None):
    """Validate provider output. Returns at most MAX_LEADS clean dicts. Never raises on bad provider data."""
    out, seen = [], set()
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        name = one_line(x.get("name"), 100)
        url = str(x.get("source_url") or "").strip()
        if not name or not is_http_url(url):
            continue
        if cited_urls is not None and url not in cited_urls:
            continue                                  # the source must be one the search actually cited
        key = (name.lower(), url)
        if key in seen:
            continue
        seen.add(key)
        phone = parse_zm_phone(x.get("phone"))
        out.append({"name": name, "area": one_line(x.get("area"), 80) or None, "phone": phone,
                    "phone_kind": phone_kind(phone), "price_text": one_line(x.get("price_text"), 80) or None,
                    "source_url": url, "source_host": host_of(url), "platform": platform_of(url)})
        if len(out) == MAX_LEADS:
            break
    return out