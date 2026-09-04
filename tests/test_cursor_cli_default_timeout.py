from __future__ import annotations

from ludex.core.organism_config import _default_provider_timeout_ms


def test_cursor_cli_receives_full_cold_start_default() -> None:
    assert _default_provider_timeout_ms("cursor_cli") == 400_000


def test_other_cli_and_non_cli_defaults_remain_scoped() -> None:
    assert _default_provider_timeout_ms("grok_cli") == 240_000
    assert _default_provider_timeout_ms("codex_cli") == 240_000
    assert _default_provider_timeout_ms("ollama") == 30_000
