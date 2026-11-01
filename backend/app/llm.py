"""Thin client for the OpenAI-compatible chat endpoint. Provider is chosen only via .env.

Every model call is reported to an optional observer as METADATA only (which job, which model, did it work, how
long). Prompts, photos and answers are never recorded. The Activity screen reads these to show what each Meta
model did in the app."""
import time
from contextlib import contextmanager
from contextvars import ContextVar

import httpx
from .config import llm_config

JOBS = {
    "agent": "Muse Spark: understands the request, picks tools",
    "web_search": "Meta Model API: web search for outside businesses",
    "flyer_vision": "Muse Spark vision: reads a flyer into a draft listing",
    "dispute_vision": "Muse Spark vision: compares a photo with the listing",
}

_job: ContextVar[str] = ContextVar("konsyer_model_job", default="agent")
_observer = None


class LLMError(RuntimeError):
    pass


def set_observer(fn):
    """fn(job, model, ok, ms, note). It must never raise into the caller; errors here are swallowed."""
    global _observer
    _observer = fn


@contextmanager
def as_job(name: str):
    token = _job.set(name)
    try:
        yield
    finally:
        _job.reset(token)


def observe(job, model, ok, ms, note=""):
    fn = _observer
    if fn is None:
        return
    try:
        fn(job, model, bool(ok), int(ms), (note or "")[:120])
    except Exception:
        pass


def chat(messages, *, tools=None, response_format=None, max_tokens=4000, timeout=180, **extra) -> dict:
    """Returns the first choice: {"message": {...}, "finish_reason": ...}.
    max_tokens must be generous: the model's hidden reasoning counts against it."""
    cfg = llm_config()
    body = {"model": cfg.model, "messages": messages, "max_tokens": max_tokens, **extra}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if response_format:
        body["response_format"] = response_format
    job, started = _job.get(), time.monotonic()
    try:
        r = httpx.post(f"{cfg.base_url}/chat/completions",
                       headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
                       json=body, timeout=timeout)
        if r.status_code != 200:
            raise LLMError(f"model API returned {r.status_code}: {r.text[:300]}")
        choice = r.json()["choices"][0]
    except Exception as e:
        observe(job, cfg.model, False, (time.monotonic() - started) * 1000, type(e).__name__)
        raise
    observe(job, cfg.model, True, (time.monotonic() - started) * 1000)
    return choice
