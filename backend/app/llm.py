"""Thin client for the OpenAI-compatible chat endpoint. Provider is chosen only via .env."""
import httpx
from .config import llm_config


class LLMError(RuntimeError):
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
    r = httpx.post(f"{cfg.base_url}/chat/completions",
                   headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
                   json=body, timeout=timeout)
    if r.status_code != 200:
        raise LLMError(f"model API returned {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]