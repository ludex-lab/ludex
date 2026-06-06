"""Hermes Interpreter — narrative ↔ structured form translator.

Phase 1 of the Hermes coordinator family (D-070 candidate). Calls
the active Hermes brain via the standard adapter (claude_cli /
codex_cli / gemini_cli), passes a schema-specific prompt, parses
the response back through the schema's validator, returns
structured data.

Failure mode: returns None on any error (no brain available,
quota exhausted, parse failure). Callers fall through to existing
behavior — Hermes failure is never fatal.
"""
from __future__ import annotations

import logging
from typing import Optional

from ludex.core.hermes.cascade import select_active_brain, HermesBrain
from ludex.core.hermes.schemas import REGISTRY

logger = logging.getLogger(__name__)


_ADAPTER_CLASSES = {
    "claude_cli": "ludex.blocks.adapters.claude_cli.ClaudeCliAdapter",
    "codex_cli": "ludex.blocks.adapters.codex_cli.CodexCliAdapter",
    "gemini_cli": "ludex.blocks.adapters.gemini_cli.GeminiCliAdapter",
}


def _instantiate_adapter(provider: str):
    """Lazy-load the adapter class for a provider."""
    path = _ADAPTER_CLASSES.get(provider)
    if not path:
        return None
    module_path, class_name = path.rsplit(".", 1)
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, class_name)()
    except Exception as e:
        logger.warning(f"Failed to instantiate adapter for {provider}: {e}")
        return None


class Interpreter:
    """Hermes Interpreter sub-service.

    `translate(source_text, target_schema, hint=None)` returns the
    parsed structured value, or None on failure.
    """

    def __init__(self):
        self._cached_brain: Optional[HermesBrain] = None

    def _resolve_brain(self) -> Optional[HermesBrain]:
        if self._cached_brain is None:
            self._cached_brain = select_active_brain()
        return self._cached_brain

    def translate(
        self,
        source_text: str,
        target_schema: str,
        *,
        field: str = "",
        hint: str = "",
    ):
        """Translate `source_text` into the structured form declared by
        `target_schema`. Returns the parsed value (typically a list
        or dict) or None on failure.

        `field` is interpolated into the prompt template when the
        schema includes a `{field}` placeholder (e.g.,
        policy_hints_yaml uses it for "in a `<field>` field"
        framing).
        """
        if not source_text or not source_text.strip():
            return None

        schema = REGISTRY.get(target_schema)
        if schema is None:
            logger.warning(f"Hermes: unknown target_schema '{target_schema}'")
            return None

        brain = self._resolve_brain()
        if brain is None:
            logger.info("Hermes: no brain available; translation skipped")
            return None

        adapter = _instantiate_adapter(brain.provider)
        if adapter is None:
            return None

        # Build prompt from schema template.
        try:
            prompt = schema.prompt_template.format(
                source_text=source_text,
                field=field or "this",
                hint=hint or "",
            )
        except KeyError:
            # Template has placeholders we didn't supply — use as-is.
            prompt = schema.prompt_template + "\n\n" + source_text

        # Call adapter (no system prompt; tools=None for headless).
        try:
            response = adapter.call(
                model=brain.model,
                prompt=prompt,
                system="",
                messages=None,
                tools=None,
            )
        except Exception as e:
            logger.warning(f"Hermes adapter call failed: {e}")
            return None

        if response is None:
            return None
        # AdapterResponse has .raw with possible error markers; bail
        # cleanly when fatigue / quota exhaustion shows up.
        raw = getattr(response, "raw", {}) or {}
        err = raw.get("error", "") if isinstance(raw, dict) else ""
        if err in ("quota_exhausted", "rate_limited", "subscription_limit"):
            logger.info(f"Hermes brain {brain.provider} fatigued ({err}); skipping")
            self._cached_brain = None  # force re-resolve next time
            return None

        content = (getattr(response, "content", "") or "").strip()
        if not content:
            return None

        parsed = schema.validator(content)
        if parsed is None:
            logger.info(
                f"Hermes: schema '{target_schema}' validator rejected output "
                f"({len(content)} chars)"
            )
            return None

        logger.info(
            f"Hermes Interpreter: translated {len(source_text)}-char source "
            f"into {target_schema} via {brain.provider}/{brain.model}"
        )
        return parsed
