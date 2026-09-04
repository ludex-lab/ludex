"""Per-model subscription limits must classify as fatigue, not as work.

2026-08-26, the morning ring: the steward's brain returned

    You've reached your Fable 5 limit. Switch to another model, or manage
    usage credits at claude.ai/settings/usage?from=cc_cli_limit_message,
    to continue.

The fatigue table only knew "<period> message limit reached" (daily /
5-hour / weekly). This shape inverts the word order and interpolates a
model name, so nothing classified it — and three watchers went blind at
once: D-068 never rested the creature, the engine never failed the turn,
and duty_runner's `[Error`-prefix guard saw plain prose and published the
sentence into the treasury ledger under the resident's own name.

The second test is the guard on the guard: the village physician wrote a
failure-type registry that same morning, and a resident writing *about*
quota walls must never be mistaken for a resident hitting one.
"""

from __future__ import annotations

from ludex.blocks.adapters.claude_cli import _detect_claude_fatigue

OBSERVED = ("You've reached your Fable 5 limit. Switch to another model, or "
            "manage usage credits at "
            "claude.ai/settings/usage?from=cc_cli_limit_message, to continue.")


def test_observed_model_limit_message_is_fatigue():
    match = _detect_claude_fatigue(OBSERVED)
    assert match is not None, "the 08-26 message must classify"
    assert match[0] == "subscription_limit"


def test_curly_apostrophe_variant_also_classifies():
    match = _detect_claude_fatigue(OBSERVED.replace("You've", "You’ve"))
    assert match is not None
    assert match[0] == "subscription_limit"


def test_url_marker_alone_classifies():
    assert _detect_claude_fatigue(
        "see claude.ai/settings/usage?from=cc_cli_limit_message") is not None


def test_other_model_names_classify():
    for model in ("Opus 5", "Sonnet 5", "Haiku 4.5"):
        assert _detect_claude_fatigue(
            f"You've reached your {model} limit.") is not None, model


def test_resident_writing_about_quota_walls_is_not_fatigue():
    """The physician's failure-type registry, 2026-08-26 — real desk work."""
    prose = (
        "## 장애 유형 대장 v0.1\n\n"
        "**Q — 쿼터 소진**: 공급자가 quota·usage-limit 관련 오류를 반환하고 "
        "같은 인증·지역의 재시도가 거부됨. 오류 원문과 시각을 보존하고 "
        "재시도 가능 시각까지 휴식시킨다. 자동 연속 재시도는 금지한다.\n"
        "A resident may report that a usage limit was reached without having "
        "reached one.\n")
    assert _detect_claude_fatigue(prose) is None
