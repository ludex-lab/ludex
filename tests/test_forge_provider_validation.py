"""Unit tests for FORGE provider validation (silent pass-through trap fix).

GAP 1: `_validate_provider` must fail loudly at assemble time for BYO-key
providers with no key, instead of letting assembly "succeed" and only breaking
at the first chat (the worst newcomer failure mode). These cover the pure
env-key-presence branches (anthropic / openai / gemini_api) — no server,
subprocess, or network. The CLI/ollama branches are exercised live via
tests/test_forge_e2e.py.
"""

import asyncio

import pytest

# web.server pulls in fastapi/uvicorn; skip cleanly when web deps are absent.
pytest.importorskip("fastapi")
from web.server import _validate_provider  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


BYOK = [
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
    ("gemini_api", "GEMINI_API_KEY"),
]


@pytest.mark.parametrize("provider,env_var", BYOK)
def test_byok_missing_key_errors_loudly(provider, env_var, monkeypatch):
    monkeypatch.delenv(env_var, raising=False)
    err = _run(_validate_provider(provider, "any-model"))
    assert err is not None
    assert env_var in err  # message names the env var the newcomer must set


@pytest.mark.parametrize("provider,env_var", BYOK)
def test_byok_present_key_passes(provider, env_var, monkeypatch):
    monkeypatch.setenv(env_var, "sk-test-not-real")
    assert _run(_validate_provider(provider, "any-model")) is None
