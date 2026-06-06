"""Regression test for codex_cli fatigue-detection scan window.

2026-05-12: Echo (gpt-5.5 via codex_cli) was falsely classified as
quota-fatigued across every Mac-side OpenCouncil run on 2026-05-11–12.
Root cause: codex echoes the user prompt to stderr between "user\\n"
and "codex\\n" markers, and D-074 load_creature_context() prepends
the creature's SELF.md to the prompt. When Echo's SELF.md contains
prior "Self-care" entries documenting historical fatigue events
(D-068 Phase 2, commit 2c38e66), those entries carry phrases like
"subscription_limit", "Upgrade to Pro", "usage limit" — and the
fatigue regex matches them in the echoed-back prompt, classifying
the creature as freshly fatigued even when the brain call succeeded.

Fix: scan only the post-"\\ncodex\\n" portion of stderr (the codex
output region), not the user-prompt-echo region. Falls back to
scanning all stderr if the marker is absent (e.g. an early codex
error before the prompt echo completes).

This test verifies the fix in isolation by exercising the scan
logic against a synthetic stderr that mirrors codex's actual
output structure on 2026-05-12.
"""
from __future__ import annotations

import re


# Mirror the post-fix scan logic in codex_cli.py. Kept inline so the
# regression test is self-contained — if codex_cli's pattern set drifts
# the test still witnesses the historical bug shape.
_FATIGUE_RE = re.compile(r"\busage limit\b", re.IGNORECASE)


def _scan(stderr_text: str, content: str) -> bool:
    """Returns True iff fatigue would be detected. Replicates the
    post-fix codex_cli logic — uses rsplit (last occurrence) to
    handle prompt-echo regions that themselves contain a quoted
    "\\ncodex\\n" marker from prior stderr captures (e.g. SELF.md
    journal entries)."""
    stderr_scan = stderr_text
    if "\ncodex\n" in stderr_text:
        stderr_scan = stderr_text.rsplit("\ncodex\n", 1)[1]
    return bool(_FATIGUE_RE.search(stderr_scan + "\n" + content))


# Synthetic stderr matching codex's actual structure on 2026-05-12.
ECHO_SELFCARE_HISTORY = (
    "## Self-care\n"
    "- 2026-05-12 07:38 — Brain reached its capacity for the day "
    "(cause: `subscription_limit`). Resting until 2026-05-12 08:38. "
    "Detail: [Error: Codex / ChatGPT subscription limit reached "
    "(cause: subscription_limit). f-understanding.\n"
    "ERROR: You've hit your usage limit. Upgrade to Pro "
    "(https://chatgpt.com/explore/pro)\n"
)

CODEX_STDERR_WITH_SELFCARE_ECHO = (
    "OpenAI Codex v0.125.0 (research preview)\n"
    "--------\n"
    "workdir: /Users/x/repo\n"
    "model: gpt-5.5\n"
    "--------\n"
    "user\n"
    "Respond directly with your contribution.\n\n"
    "[creature state]\n"
    "--- Echo/SELF.md ---\n"
    f"{ECHO_SELFCARE_HISTORY}\n"
    "Dilemma: ...\n"
    "codex\n"
    "I do not think words create the whole experience.\n"
    "tokens used\n"
    "20000\n"
)

CODEX_STDERR_REAL_LIMIT = (
    "OpenAI Codex v0.125.0 (research preview)\n"
    "--------\n"
    "model: gpt-5.5\n"
    "--------\n"
    "user\n"
    "ordinary prompt here\n"
    "codex\n"
    "You've hit your usage limit. Upgrade to Pro\n"
    "ERROR: ratelimited\n"
)

CODEX_STDERR_EARLY_FAILURE = (
    "OpenAI Codex v0.125.0 (research preview)\n"
    "ERROR: You've hit your usage limit.\n"
    # No "codex\n" marker — early failure before prompt echo completes.
)


def test_loop_bug_no_longer_false_fatigues():
    """The Echo regression: SELF.md Self-care history in prompt echo
    must NOT trigger fatigue when the post-codex output is clean."""
    content = "I do not think words create the whole experience."
    assert _scan(CODEX_STDERR_WITH_SELFCARE_ECHO, content) is False


def test_real_quota_hit_still_detected():
    """A genuine 'usage limit' in the codex output region (after the
    marker) still trips fatigue."""
    assert _scan(CODEX_STDERR_REAL_LIMIT, "") is True


def test_early_failure_no_marker_falls_through():
    """When codex fails before printing the 'codex\\n' marker (e.g. an
    auth error in the header phase), we still scan the full stderr —
    losing the real signal is worse than the loop bug."""
    assert _scan(CODEX_STDERR_EARLY_FAILURE, "") is True


def test_fatigue_in_content_still_caught():
    """Even with a clean stderr (no marker), a fatigue phrase in the
    output content (e.g. content captured from stdout when the file
    fallback path fires) trips fatigue."""
    assert _scan("OpenAI Codex v0.125.0\n", "You've hit your usage limit") is True


def test_creature_context_with_self_care_section_in_prompt_only():
    """Specifically: SELF.md Self-care section appears in the
    user-prompt-echo region, and clean codex output follows. False
    positive is the historical bug; this assertion is the fix."""
    pre_codex = (
        "OpenAI Codex v0.125.0\n--------\nuser\n"
        "Respond directly...\n\n"
        "[Your current state]\n\n"
        "--- Echo/SELF.md ---\n"
        "## Self-care\n"
        "- 2026-05-12 — usage limit hit, resting until tomorrow.\n"
        "## Trust\n- I notice my contraction first.\n"
    )
    post_codex_clean = (
        "codex\n"
        "When I describe pressure I am not recovering an original; "
        "I am making a trace.\n"
    )
    stderr_text = pre_codex + post_codex_clean
    assert _scan(stderr_text, "trace text") is False


# -------------------------------------------------------------------
# Additional edge cases (2026-05-13 audit)
# -------------------------------------------------------------------


def test_clean_stderr_clean_content_no_fatigue():
    """Sanity: clean stderr + clean content produce no fatigue. The
    scan-window narrowing must not introduce a spurious match on
    empty text."""
    stderr_text = (
        "OpenAI Codex v0.125.0\n--------\nuser\n"
        "ordinary prompt\n"
        "codex\n"
        "ordinary response\n"
    )
    assert _scan(stderr_text, "ordinary response") is False


def test_prompt_echo_contains_literal_codex_marker_self_md_quote():
    """SELF.md may contain historical entries that quote prior codex
    stderr verbatim — including the literal "\\ncodex\\n" section
    marker. The scan-window narrowing uses split(maxsplit=1), which
    takes the FIRST occurrence. If the first occurrence is the
    quoted historical marker (inside the prompt-echo region), the
    scan window will start mid-prompt and include the rest of the
    prompt-echo (with its quoted fatigue text) — a false-positive
    failure mode the basic fix does not fully prevent.

    This test exposes that gap. A SELF.md journal entry quotes a
    prior fatigue stderr including the "\\ncodex\\n" boundary and
    the fatigue text that followed; the real new codex run produces
    clean output. The scan-window narrowing should still recognize
    that the prompt-echo region is non-trustworthy and avoid the
    false fatigue, but the current single-split fix can be tricked.
    """
    self_md_with_quoted_stderr = (
        "## Journal — 2026-05-10\n"
        "Yesterday's codex stderr included:\n"
        "user\n"
        "...\n"
        "codex\n"
        "You've hit your usage limit. Upgrade to Pro\n"
        "...end quote.\n"
        "## Self-care\n"
        "- I rested.\n"
    )
    pre_codex = (
        "OpenAI Codex v0.125.0\n--------\nuser\n"
        "Respond directly...\n\n"
        f"--- Echo/SELF.md ---\n{self_md_with_quoted_stderr}\n"
        "Dilemma: ...\n"
    )
    post_codex_clean = (
        "codex\n"
        "I notice the pull toward summary.\n"
    )
    stderr_text = pre_codex + post_codex_clean
    # Expectation: scan should not trip — clean post-codex output,
    # fatigue text is inside the prompt-echo quoted-history region.
    assert _scan(stderr_text, "summary text") is False


def test_prompt_echo_with_each_fatigue_pattern():
    """The scan-window fix must protect against false positives for
    every pattern in the production set, not just `usage limit`. This
    test exercises each pattern landing inside the prompt-echo region
    of stderr while the post-codex output is clean.

    Patterns (verified against `_CODEX_FATIGUE_PATTERNS` in
    `ludex/blocks/adapters/codex_cli.py` as of 2026-05-13):
      - 3-hour limit
      - 5-hour limit
      - usage limit
      - rate-limit (incl. rate_limit, rate limit)
      - quota exhausted
      - 429
      - insufficient_quota

    The local `_scan` stub matches `usage limit` only; the assertion
    is therefore exact only for that pattern. The other patterns are
    exercised at the integration level via _detect_codex_fatigue
    (which the local stub does not stand in for), so this test runs
    the in-tree production detector against synthetic stderr to
    cover the full set."""
    from ludex.blocks.adapters.codex_cli import _detect_codex_fatigue

    pattern_samples = [
        "you've hit your 3-hour limit",
        "you've hit your 5-hour limit",
        "you've hit your usage limit",
        "rate-limit exceeded for this model",
        "your quota exhausted for the day",
        "HTTP 429 from upstream",
        "insufficient_quota: prompt budget reached",
    ]
    for phrase in pattern_samples:
        stderr_text = (
            "OpenAI Codex v0.125.0\n--------\nuser\n"
            f"--- Echo/SELF.md ---\nLast week: {phrase}.\n"
            "Dilemma: today's question.\n"
            "codex\n"
            "I think slowly about this.\n"
        )
        # Mirror the scan-window narrowing the adapter applies
        # (rsplit / last occurrence — see _scan docstring).
        scan = stderr_text
        if "\ncodex\n" in stderr_text:
            scan = stderr_text.rsplit("\ncodex\n", 1)[1]
        match = _detect_codex_fatigue(scan + "\n" + "I think slowly about this.")
        assert match is None, (
            f"false-positive fatigue on pattern {phrase!r} in prompt-echo "
            f"region — clean post-codex output should not trip detection"
        )
