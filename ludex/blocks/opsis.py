"""Opsis — visual sensing organ (D-048).

Phase A implementation. Three layers:
- Acquisition: file path or (optional) URL via Playwright
- Transduction: normalize to a file path on disk
- Interpretation: Claude CLI interpreter → text description

Phase A note: even VLM-capable brains go through the Logos-fallback
path here because existing adapters do not yet support image input.
Native channel (direct image → brain) lands in Phase B after
adapter image support is added. D-048 framing stands; current
code is the conservative path.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpsisResult:
    """What Opsis returns to the creature after sensing one scene."""
    description: str                       # text the creature "sees"
    source: str                             # original path or URL
    channel: str                            # "logos_fallback" (Phase A) or "native"
    interpreter: str = ""                   # model that produced description
    acquired_path: str = ""                 # local path to acquired image
    latency_ms: float = 0.0
    error: str = ""                         # non-empty on failure
    raw: dict = field(default_factory=dict)


class OpsisBlock(Block):
    """Visual sensing organ. Phase A — file + (optional) URL input,
    Claude CLI interpreter for text description.

    provides: see
    requires: (none directly; uses external adapters)

    Invocation is caretaker-authorized (field or script supplies the
    source). Opsis does not auto-trigger.
    """

    name = "opsis"
    provides = [
        Port("see", description="Acquire and interpret a visual source"),
    ]
    requires = []

    def __init__(self, interpreter_model: str = "claude-opus-4-6",
                 cache_dir: str = ""):
        super().__init__()
        self._interpreter_model = interpreter_model
        self._cache_dir = cache_dir

    def on_attach(self):
        if not self._cache_dir and self._config:
            habitat = self._config.get("habitat_dir", "")
            if habitat:
                self._cache_dir = str(Path(habitat) / "opsis_cache")

    # --- Provides: see ---

    def handle_see(self, source: str, purpose: str = "") -> OpsisResult:
        """Sense a visual source.

        Args:
            source: a local file path OR a URL (http/https). URL
                handling requires Playwright to be installed; falls
                back to an error result if not.
            purpose: optional short string describing why the creature
                is looking. Shapes the interpreter's prioritization
                (e.g. "checking whether the page loaded correctly"
                vs "examining a chart for trend direction").

        Returns:
            OpsisResult with description or error.
        """
        t0 = time.perf_counter()
        try:
            # --- Acquisition ---
            acquired_path = self._acquire(source)
            if acquired_path is None:
                return OpsisResult(
                    description="",
                    source=source,
                    channel="logos_fallback",
                    error=f"acquisition failed for {source!r}",
                    latency_ms=(time.perf_counter() - t0) * 1000,
                )

            # --- Interpretation (Phase A: always Logos fallback) ---
            description, interpreter_meta = self._interpret(
                acquired_path, purpose=purpose
            )
            elapsed = (time.perf_counter() - t0) * 1000

            result = OpsisResult(
                description=description,
                source=source,
                channel="logos_fallback",
                interpreter=interpreter_meta.get("model", self._interpreter_model),
                acquired_path=str(acquired_path),
                latency_ms=elapsed,
                raw=interpreter_meta,
            )

            # Emit trace span
            try:
                from ludex.core import trace as _tr
                _tr.emit_opsis_invoked(
                    self._organism,
                    source=source,
                    channel="logos_fallback",
                    purpose=purpose,
                    description_len=len(description),
                    latency_ms=elapsed,
                )
            except Exception:
                pass

            # Perception-action Step 1: sampling-aware consolidation
            # (routes a SensoryEvent through the gate; gate decides
            # whether to write an episodic memory entry, per
            # docs/perception-action-systems-design.md §8 Step 1).
            try:
                from ludex.core import sensory_consolidation as _sc
                from ludex.core import trace as _tr
                _sc.consolidate_observation(
                    self._organism,
                    _sc.SensoryEvent(
                        source_kind="opsis",
                        content=description,
                        source=source,
                        purpose=purpose,
                        channel="logos_fallback",
                        field_name=_tr.current_field(),
                        metadata={
                            "interpreter": interpreter_meta.get("model", ""),
                            "latency_ms": round(elapsed, 1),
                            "acquired_path": str(acquired_path),
                        },
                    ),
                )
            except Exception as e:
                logger.debug(f"opsis consolidation skipped: {e}")

            return result

        except Exception as e:
            logger.warning(f"Opsis.see({source!r}) failed: {e}")
            return OpsisResult(
                description="",
                source=source,
                channel="logos_fallback",
                error=str(e),
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

    # --- Acquisition router ---

    def _acquire(self, source: str) -> Optional[Path]:
        """Route to the appropriate acquisition adapter."""
        if not source:
            return None
        s = source.strip()
        if s.startswith("http://") or s.startswith("https://"):
            return self._acquire_url(s)
        # Treat as file path
        return self._acquire_file(s)

    def _acquire_file(self, path_str: str) -> Optional[Path]:
        from ludex.blocks.opsis_adapters.file_source import acquire_file
        return acquire_file(path_str)

    def _acquire_url(self, url: str) -> Optional[Path]:
        try:
            from ludex.blocks.opsis_adapters.url_source import acquire_url
        except ImportError as e:
            logger.warning(
                f"Opsis URL acquisition unavailable (missing dependency): {e}"
            )
            return None
        out_dir = Path(self._cache_dir) if self._cache_dir else Path("/tmp/opsis_cache")
        out_dir.mkdir(parents=True, exist_ok=True)
        return acquire_url(url, out_dir)

    # --- Interpretation ---

    def _interpret(self, image_path: Path, purpose: str = "") -> tuple[str, dict]:
        """Always uses Claude CLI in Phase A."""
        from ludex.blocks.opsis_adapters.claude_cli_interpreter import interpret
        return interpret(image_path, purpose=purpose,
                         model=self._interpreter_model)
