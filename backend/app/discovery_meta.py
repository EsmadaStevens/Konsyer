"""Discovery through the Meta Model API: web search grounding on Muse Spark (a Meta model feature).
The model's answer is only a list of CANDIDATES. Plain code re-validates every field, and a lead's source page
must be one the search actually visited (annotations or search-call items)."""
import json
import os

import httpx

from . import config  # noqa: F401  (importing it loads the project .env)
from .discovery import DiscoveryError

PROMPT = (
    "Use web search to find real businesses offering '{service}' in {area}, Lusaka, Zambia. "
    "Only include businesses that are clearly in Zambia. Prefer ones with a phone number. Do not spend more than "
    "about eight searches. Return ONLY a JSON array (at most 6 items, no other text). Each item has these keys: "
    "name, area, phone (exactly as shown on the source, or null), whatsapp_mentioned (true or false), "
    "price_text (the exact price wording from the source, or null; never estimate), "
    "price_for (what that price is for, as the source says, or null if the source does not say), "
    "source_url (the page where you saw it). "
    "Use null for anything the source does not show. Do not invent businesses, numbers or prices."
)


def urls_in(obj, found=None):
    found = set() if found is None else found
    if isinstance(obj, str):
        if obj.startswith(("http://", "https://")):
            found.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            urls_in(v, found)
    elif isinstance(obj, list):
        for v in obj:
            urls_in(v, found)
    return found


def collect(resp):
    """(answer text, set of urls the search cited or visited) from a Responses API body."""
    text, urls = [], set()
    for item in resp.get("output", []) or []:
        if item.get("type") == "web_search_call":
            urls_in(item, urls)
        elif item.get("type") == "message":
            for part in item.get("content", []) or []:
                if part.get("type") == "output_text":
                    text.append(part.get("text", ""))
                    for a in part.get("annotations", []) or []:
                        urls_in(a, urls)
    return "\n".join(text), urls


def parse_json_array(text):
    a, b = text.find("["), text.rfind("]")
    if a < 0 or b <= a:
        return None
    try:
        data = json.loads(text[a:b + 1])
    except ValueError:
        return None
    return [x for x in data if isinstance(x, dict)]


class MetaDiscovery:
    name, is_sample, require_contact = "meta", False, True

    def __init__(self, api_key=None, base_url=None, model=None, timeout=150, post=None):
        self.api_key = (api_key or os.environ.get("META_API_KEY") or os.environ.get("MODEL_API_KEY") or "").strip()
        if not self.api_key:
            raise ValueError("DISCOVERY_PROVIDER=meta needs META_API_KEY in .env")
        self.base_url = (base_url or os.environ.get("META_API_BASE", "https://api.meta.ai/v1")).rstrip("/")
        self.model = model or os.environ.get("META_SEARCH_MODEL", "muse-spark-1.3")
        self.timeout = timeout
        self._post = post or httpx.post

    def _body(self, service, area):
        body = {"model": self.model, "input": PROMPT.format(service=service, area=area),
                "tools": [{"type": "web_search"}]}
        if os.environ.get("DISCOVERY_MAX_TOOL_CALLS", "").isdigit():
            body["max_tool_calls"] = int(os.environ["DISCOVERY_MAX_TOOL_CALLS"])
        if os.environ.get("DISCOVERY_REASONING_EFFORT"):
            body["reasoning"] = {"effort": os.environ["DISCOVERY_REASONING_EFFORT"]}
        return body

    def search_with_sources(self, service, area):
        r = self._post(f"{self.base_url}/responses", timeout=self.timeout, json=self._body(service, area),
                       headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        if r.status_code != 200:
            raise DiscoveryError(f"search API returned {r.status_code}")
        text, cited = collect(r.json())
        leads = parse_json_array(text)
        if leads is None:
            raise DiscoveryError("could not read the search result")
        if os.environ.get("DISCOVERY_REQUIRE_CITED", "1") == "0":
            return leads, None
        if not cited:
            raise DiscoveryError("the search returned no sources to check")
        return leads, cited

    def search(self, service, area):
        return self.search_with_sources(service, area)[0]