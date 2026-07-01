"""Brain class taxonomy — narrative / structured / hybrid / unknown.

Surfaced 2026-05-01 from the Wick `distill_dead` investigation: brain
identity (provider + model) groups naturally into a small set of
operational classes that determine *what kinds of fields a creature
can run*. Different from `brain_capabilities` (D-072 — fine-grained
tool/json/multimodal probing); brain_class is the coarse routing
class:

- `narrative` — high turn latency, prose-first emission.
  Suited to: distill, reflection, narrative fields (Wilderness/
  Council), training-data generation.
  Not suited to: real-time game fields with strict turn budgets
  (timeouts cascade into parse failures).
  Examples: gemini-cli (any model — "Explain Before Acting" mandate).

- `structured` — fast turn latency, function-call / JSON-shaped
  emission. Suited to real-time game fields. May be brittle on rich
  prose distill.
  Examples: ollama SLMs (qwen/exaone/gemma), claude-haiku-4-5,
  claude-flash-*, claude-sonnet-4-5.

- `hybrid` — frontier brains; handle both narrative and structured
  well. More expensive per turn.
  Examples: claude-opus-4-7, claude-sonnet-4-6, gpt-5.5.

- `unknown` — no rule matched; treat conservatively.

Routing is coarse on purpose. Caretakers override by setting
`brain.class` explicitly in `ludex.yaml`.
"""

from __future__ import annotations

NARRATIVE = "narrative"
STRUCTURED = "structured"
HYBRID = "hybrid"
UNKNOWN = "unknown"

BRAIN_CLASSES = (NARRATIVE, STRUCTURED, HYBRID, UNKNOWN)


def classify_brain(provider: str, model: str = "") -> str:
    """Map (provider, model) to one of the brain classes. Routing is
    coarse — caretakers override by setting `brain.class` explicitly."""
    p = (provider or "").lower()
    m = (model or "").lower()

    # gemini-cli's PromptProvider mandate makes everything narrative
    # regardless of model — see `project_gemini_cli_usage_policy.md`.
    if p == "gemini_cli":
        return NARRATIVE

    # Frontier hybrid models — bidirectional capability.
    if "opus" in m or "sonnet-4-6" in m or "sonnet-5" in m or "gpt-5.5" in m:
        return HYBRID

    # Workhorse structured: haiku, flash, sonnet-4-5 — fast, function-call.
    if "haiku" in m or "flash" in m or "sonnet-4-5" in m:
        return STRUCTURED

    # Ollama SLMs route as structured (function-call/JSON tuned).
    if p == "ollama":
        return STRUCTURED

    # Claude SDK/CLI without a specific model match — assume hybrid
    # (opus is the safe default for unspecified Claude).
    if p in ("claude_cli", "claude_sdk", "anthropic"):
        return HYBRID

    # Codex CLI defaults to gpt-5.5 — hybrid.
    if p == "codex_cli":
        return HYBRID

    return UNKNOWN
