"""Tests must not depend on your .env: no real searches, no demo phone, no tuning switches."""
import pytest


@pytest.fixture(autouse=True)
def _hermetic_discovery_env(monkeypatch):
    for name in ("KONSYER_DEMO_LEAD_PHONE", "DISCOVERY_SHOW_PHONES", "DISCOVERY_MAX_TOOL_CALLS",
                 "DISCOVERY_REASONING_EFFORT", "DISCOVERY_REQUIRE_CITED", "META_API_BASE", "META_SEARCH_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISCOVERY_PROVIDER", "mock")