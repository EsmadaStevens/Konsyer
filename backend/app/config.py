"""Single place for model endpoint settings. Switch providers via .env only."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    api_key: str


def llm_config() -> LLMConfig:
    key = os.environ.get("LLM_API_KEY")
    if not key:
        raise SystemExit("LLM_API_KEY missing. Copy .env.example to .env and fill it in.")
    return LLMConfig(
        base_url=os.environ.get("LLM_BASE_URL", "https://inference.baseten.co/v1").rstrip("/"),
        model=os.environ.get("LLM_MODEL", "meta-models/muse-spark-open-1026"),
        api_key=key,
    )