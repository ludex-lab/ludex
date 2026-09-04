"""Regression guard for per-creature brain.auth — the CLI subprocess env.

A real Anthropic billing email (2026-06-18) traced to `env={**os.environ}` in the
CLI adapters: Ludex loads .env into os.environ, so an API key leaked into every
subprocess and the CLI billed the API instead of the logged-in subscription. The
fix made auth a birth-time per-creature decision (brain.auth) honored at env-build.
These tests lock that behavior so the leak can't silently return.
"""
import os

import pytest

from ludex.blocks.adapters._cli_env import cli_subprocess_env
from ludex.blocks.provider import ProviderBlock

ALL_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")


@pytest.fixture(autouse=True)
def _inject_dummy_keys(monkeypatch):
    """Put all three provider keys in the env so strip/keep is observable."""
    for k in ALL_KEYS:
        monkeypatch.setenv(k, f"DUMMY-{k}")
    yield


@pytest.mark.parametrize("provider,key", [
    ("claude_cli", "ANTHROPIC_API_KEY"),
    ("codex_cli", "OPENAI_API_KEY"),
    ("gemini_cli", "GEMINI_API_KEY"),
    ("agy_cli", "GEMINI_API_KEY"),
])
def test_subscription_strips_key(provider, key):
    env = cli_subprocess_env(provider, "subscription")
    assert key not in env, f"{provider} subscription must strip {key} (else API is billed)"


@pytest.mark.parametrize("provider,key", [
    ("claude_cli", "ANTHROPIC_API_KEY"),
    ("codex_cli", "OPENAI_API_KEY"),
    ("gemini_cli", "GEMINI_API_KEY"),
    ("agy_cli", "GEMINI_API_KEY"),
])
def test_api_keeps_key(provider, key):
    env = cli_subprocess_env(provider, "api")
    assert env.get(key) == f"DUMMY-{key}", f"{provider} api must keep {key}"


@pytest.mark.parametrize("provider,key,stripped_by_default", [
    ("claude_cli", "ANTHROPIC_API_KEY", True),   # default subscription — never bill
    ("codex_cli", "OPENAI_API_KEY", True),       # default subscription
    ("grok_cli", "XAI_API_KEY", True),           # default subscription
    ("gemini_cli", "GEMINI_API_KEY", False),     # default api — consumer tier deprecated
    ("agy_cli", "GEMINI_API_KEY", True),         # default subscription — Nova re-brained here to reach it
])
def test_unset_auth_uses_provider_default(provider, key, stripped_by_default):
    env = cli_subprocess_env(provider, "")
    assert (key not in env) == stripped_by_default


def test_a_provider_defaults_to_api_only_where_subscription_has_no_path():
    """The default is a claim about what works, and it must be the safe one.

    agy_cli defaulted to api on the 06-18 reading that the gemini consumer tier
    was gone. Nova's 07-20 re-brain moved her onto agy_cli precisely to reach
    subscription, so from then on the api default was the unsafe one: a creature
    born on agy with no explicit auth would silently bill a path that has a
    logged-in session available.
    """
    from ludex.blocks.adapters._cli_env import _PROVIDER_AUTH
    billing_by_default = {p for p, (_, d) in _PROVIDER_AUTH.items() if d == "api"}
    assert billing_by_default == {"gemini_cli"}, billing_by_default


def test_only_the_providers_own_key_is_stripped():
    """Stripping claude's key must never touch OpenAI/Gemini keys, and vice-versa."""
    env = cli_subprocess_env("claude_cli", "subscription")
    assert "ANTHROPIC_API_KEY" not in env
    assert env.get("OPENAI_API_KEY") == "DUMMY-OPENAI_API_KEY"
    assert env.get("GEMINI_API_KEY") == "DUMMY-GEMINI_API_KEY"


def test_auth_is_case_insensitive():
    assert "ANTHROPIC_API_KEY" not in cli_subprocess_env("claude_cli", "Subscription")
    assert "ANTHROPIC_API_KEY" in cli_subprocess_env("claude_cli", "API")


def test_pythonioencoding_always_set():
    assert cli_subprocess_env("claude_cli", "api")["PYTHONIOENCODING"] == "utf-8"


def test_unknown_provider_is_passthrough():
    """A provider with no key mapping keeps the full env (no accidental strip)."""
    env = cli_subprocess_env("ollama", "subscription")
    for k in ALL_KEYS:
        assert k in env


@pytest.mark.parametrize("provider", ["claude_cli", "codex_cli", "gemini_cli", "agy_cli"])
def test_provider_block_threads_auth_to_adapter(provider):
    pb = ProviderBlock(provider=provider, model="x", cwd="/tmp", auth="api")
    assert pb._adapter._auth == "api"
    assert pb._init_config["auth"] == "api"


def test_claude_sdk_excluded_from_auth_kwarg():
    """claude_sdk is in-process and forwards **kwargs to BaseAdapter, which rejects
    `auth` — the provider must NOT pass auth to it (would raise TypeError)."""
    pb = ProviderBlock(provider="claude_sdk", model="sonnet", cwd="/tmp", auth="api")
    assert pb._adapter is not None  # built without crashing
