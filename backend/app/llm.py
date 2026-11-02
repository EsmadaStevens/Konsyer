"""Thin client for the OpenAI-compatible chat endpoint. Provider is chosen only via .env.

Every model call is reported to an optional observer as METADATA only (which job, which model, did it work, how
long). Prompts, photos and answers are never recorded. The Activity screen reads these to show what each Meta
model did in the app."""
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

import httpx
from .config import llm_config

JOBS = {
    "agent": "Muse Spark: understands the request, picks tools",
    "web_search": "Meta Model API: web search for outside businesses",
    "flyer_vision": "Muse Spark vision: reads a flyer into a draft listing",
    "listing_vision": "Muse Spark vision: describes a product photo as a listing",
    "dispute_vision": "Muse Spark vision: compares a photo with the listing",
}

_job: ContextVar[str] = ContextVar("konsyer_model_job", default="agent")
_input_name: ContextVar = ContextVar("konsyer_input_name", default=None)
_observer = None


class LLMError(RuntimeError):
    pass


class ReplayMiss(LLMError):
    """Keyless replay mode was asked something it has no recording for."""


# ---------- record and replay (so the demo can run without a model key) ----------
# KONSYER_RECORD=path  appends every successful live model call to a trace file.
# KONSYER_REPLAY=path  answers model calls from that file instead of calling a model.
# The trace holds the model's answers and a hash of each request. Photos are never stored, only their hashes.
_replay_lock = threading.Lock()
_replay = {"path": None, "mtime": None, "data": {}, "used": {}}


def replaying() -> bool:
    return bool(os.environ.get("KONSYER_REPLAY", "").strip())


def available() -> bool:
    """True if a model call can be answered: a key is set, or a recording is being replayed."""
    if os.environ.get("KONSYER_STUB") == "1":
        return False
    return replaying() or bool(os.environ.get("LLM_API_KEY"))


def mode() -> str:
    if os.environ.get("KONSYER_STUB") == "1":
        return "stub"
    if replaying():
        return "replay"
    return "live" if os.environ.get("LLM_API_KEY") else "stub"


def _norm(text) -> str:
    # a full stop, "!" or "?" at the end must not matter: "...budget is K300." finds the recording of "...K300"
    return re.sub(r"\s+", " ", str(text or "")).strip().lower().rstrip(" .!?")


def request_key(job, messages):
    """(hash, readable label). The key depends only on what the user did, not on prompts or dates, so a trace stays
    valid when a prompt is reworded: the job, the user's last message, how many tool results came after it (which
    step of the conversation this is) and the hashes of any images."""
    last = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=None)
    parts, label, step = [], "", 0
    if last is not None:
        content = messages[last].get("content")
        step = sum(1 for m in messages[last + 1:] if m.get("role") == "tool")
        if isinstance(content, str):
            parts.append(_norm(content))
            label = content[:80]
        else:
            for c in content or []:
                if c.get("type") == "image_url":
                    name = _input_name.get()
                    parts.append("file:" + name if name else
                                 "img:" + hashlib.sha256(str(c["image_url"]["url"]).encode()).hexdigest()[:16])
                    label = label or (f"(image {name})" if name else "(image)")
    raw = json.dumps([job, step, parts], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24], f"{job} step {step}: {label}"


def _load_trace(path):
    mtime = os.path.getmtime(path)
    if _replay["path"] == path and _replay["mtime"] == mtime:
        return _replay["data"]
    data = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                row = json.loads(line)
                data.setdefault(row["key"], []).append(row["response"])
    _replay.update(path=path, mtime=mtime, data=data, used={})
    return data


def _replayed(job, messages):
    path = os.environ["KONSYER_REPLAY"].strip()
    try:
        data = _load_trace(path)
    except OSError:
        raise LLMError(f"The replay file {path} could not be read.")
    key, label = request_key(job, messages)
    with _replay_lock:
        options = data.get(key)
        if not options:
            raise ReplayMiss("Replay mode: there is no recording for this exact input, so nothing was asked of a "
                             "model. Try one of the recorded examples (see the README), or add LLM_API_KEY to use "
                             "the live models.")
        n = _replay["used"].get(key, 0)
        _replay["used"][key] = n + 1
        return options[n % len(options)]  # the same question asked again gets the next recorded answer, then cycles


def _record(job, messages, choice):
    path = os.environ.get("KONSYER_RECORD", "").strip()
    if not path:
        return
    key, label = request_key(job, messages)
    with _replay_lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"job": job, "key": key, "label": label, "response": choice}, ensure_ascii=False) + "\n")


def set_observer(fn):
    """fn(job, model, ok, ms, note). It must never raise into the caller; errors here are swallowed."""
    global _observer
    _observer = fn


@contextmanager
def input_name(name):
    """The uploaded file's name. The app shrinks and re-encodes photos before sending them, so a recording is matched
    by file name rather than by the image bytes."""
    token = _input_name.set(os.path.basename(str(name or "")).lower() or None)
    try:
        yield
    finally:
        _input_name.reset(token)


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
    if replaying():
        job, started = _job.get(), time.monotonic()
        try:
            choice = _replayed(job, messages)
        except Exception as e:
            observe(job, "replay", False, (time.monotonic() - started) * 1000, type(e).__name__)
            raise
        observe(job, "replay (recorded)", True, (time.monotonic() - started) * 1000)
        return choice
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
    _record(job, messages, choice)
    return choice
