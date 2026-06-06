"""D-072 Phase A pillar 1 — birth-time brain capability probe.

Runs a tiny prompt through a freshly-instantiated provider adapter
to verify what the brain can actually do *before* the creature is
relied on for production work. Result is cached on the creature's
config (brain_capabilities list + capability_probed_brain key)
and re-probed only when the brain identity changes.

Probe is wall-clock-capped (default 30s) and never raises — a
brain that hangs or errors simply receives an empty capability
set, and field-level adapters route accordingly (D-072 pillar 3).
"""

from __future__ import annotations

import json
import logging
import re
import time

logger = logging.getLogger(__name__)


# Production-shape probe: mirrors the `[LxM] Match: ... | Agent: ... |
# Turn: ...` pattern that triggers agentic narration on gemini-cli (and
# similar opinionated CLI-wrapped brains). A simple "reply with JSON"
# prompt is *too easy* — it passes on brains that fail in real LxM
# matches. The header keeps the trigger surface; the body is
# minimal so a compliant brain returns the probe payload directly.
_PROBE_PROMPT = (
    "[LxM] Match: birth-probe | Agent: probe | Turn: 1\n"
    "Phase: probe. Reply with this exact JSON object and "
    "absolutely nothing else (no narration, no plan, no preamble):\n"
    '{"ping":"pong","brain":"%s"}'
)


def probe_brain_capabilities(
    provider_name: str,
    model: str,
    cwd: str = "",
    timeout_ms: int = 30000,
) -> dict:
    """Return a capability snapshot for (provider × model).

    Keys:
      - json_emit (bool): brain returned valid JSON matching the probe.
      - narrative (bool): brain returned non-empty text (any).
      - probed_at (float): epoch timestamp.
      - model (str), transport (str), elapsed_ms (int).
      - error (str, optional): exception message if the call failed.

    Never raises. Adapter import failures surface as error="...".
    """
    snapshot = {
        "json_emit": False,
        "narrative": False,
        "probed_at": time.time(),
        "model": model,
        "transport": provider_name,
        "elapsed_ms": 0,
    }

    try:
        adapter = _build_adapter(provider_name, cwd, timeout_ms)
    except Exception as e:
        snapshot["error"] = f"adapter init: {type(e).__name__}: {e}"
        return snapshot

    if adapter is None:
        snapshot["error"] = f"unknown provider: {provider_name!r}"
        return snapshot

    prompt = _PROBE_PROMPT % model
    start = time.time()
    try:
        result = adapter.call(model=model, prompt=prompt)
        content = (getattr(result, "content", "") or "").strip()
    except Exception as e:
        snapshot["error"] = f"probe call: {type(e).__name__}: {e}"
        snapshot["elapsed_ms"] = int((time.time() - start) * 1000)
        return snapshot

    snapshot["elapsed_ms"] = int((time.time() - start) * 1000)
    snapshot["narrative"] = bool(content)
    snapshot["json_emit"] = _content_matches_probe(content)
    return snapshot


def capability_set(snapshot: dict) -> list[str]:
    """Convert a probe snapshot to the canonical capability list
    that lands on `OrganismConfig.brain_capabilities`."""
    caps: list[str] = []
    if snapshot.get("json_emit"):
        caps.append("json_emit")
    if snapshot.get("narrative"):
        caps.append("narrative")
    return caps


def _build_adapter(provider_name: str, cwd: str, timeout_ms: int):
    """Construct a bare adapter instance for the probe.

    Mirrors `ProviderBlock._create_adapter` but minimal: no api_key
    plumbing (probes don't need it for CLI providers; ollama uses
    default base_url; OpenAI/Anthropic API probes are deferred —
    they require a key path which the probe shouldn't fish for).
    """
    from ludex.blocks.adapters.ollama import OllamaAdapter
    from ludex.blocks.adapters.claude_cli import ClaudeCliAdapter
    from ludex.blocks.adapters.claude_sdk import ClaudeSdkAdapter
    from ludex.blocks.adapters.gemini_cli import GeminiCliAdapter
    from ludex.blocks.adapters.agy_cli import AgyCliAdapter
    from ludex.blocks.adapters.codex_cli import CodexCliAdapter

    REGISTRY = {
        "ollama": OllamaAdapter,
        "claude_cli": ClaudeCliAdapter,
        "claude_sdk": ClaudeSdkAdapter,
        "gemini_cli": GeminiCliAdapter,
        "agy_cli": AgyCliAdapter,
        "codex_cli": CodexCliAdapter,
    }
    cls = REGISTRY.get(provider_name)
    if cls is None:
        return None

    kwargs: dict = {"timeout_ms": timeout_ms}
    if provider_name in ("claude_cli", "claude_sdk", "gemini_cli", "agy_cli", "codex_cli") and cwd:
        kwargs["cwd"] = cwd
    return cls(**kwargs)


def _content_matches_probe(content: str) -> bool:
    """True iff content contains a JSON object with ping=pong.

    Tolerant: accepts bare JSON, fenced ```json ... ``` blocks, and
    JSON nested anywhere in narrative output. The probe semantic is
    'can the brain be steered to emit JSON when asked', so finding
    the structured payload anywhere in the response counts.
    """
    if not content:
        return False
    # Strip code fences if present.
    fenced = re.search(r"```(?:json)?\s*(\{[^`]+?\})\s*```", content, re.DOTALL)
    candidates = []
    if fenced:
        candidates.append(fenced.group(1))
    # Also look for inline JSON objects mentioning "ping".
    for m in re.finditer(r"\{[^{}]*\"ping\"[^{}]*\}", content):
        candidates.append(m.group(0))
    for raw in candidates:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("ping") == "pong":
                return True
        except (json.JSONDecodeError, ValueError):
            continue
    return False
