"""Akoué — auditory sensing organ (D-049).

Phase A implementation. Three layers matching Opsis (D-048):
- Acquisition: audio file (live-mic adapter is Phase B)
- Transduction: normalize to a PCM-compatible path (ffmpeg delegation)
- Interpretation: Whisper for speech → text

Phase A note: all brains route through the Logos-fallback
(Whisper transcription → text). Native audio channel for
audio-capable brains (gpt-4o, gemini audio variants) lands in
Phase B when adapters gain audio input I/O. Claude does not
handle audio natively, so Akoué's interpreter cannot be Claude CLI
the way Opsis's Phase A interpreter was.

Scope: speech only. Non-speech audio (music, ambient, prosody)
is Phase C per D-049. Live-mic capture (with start/stop markers)
is Phase B per D-049.

Whisper backend: tries `openai-whisper` first. If unavailable,
returns an error result pointing at install instructions. Same
graceful-degradation pattern as Opsis's Playwright URL adapter.
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
class AkoueResult:
    """What Akoué returns to the creature after sensing audio."""
    transcription: str                     # text the creature "heard"
    source: str                             # original path
    channel: str                            # "logos_fallback" (Phase A)
    interpreter: str = ""                   # whisper model used
    acquired_path: str = ""                 # local path to acquired audio
    duration_s: float = 0.0                 # audio duration
    latency_ms: float = 0.0
    error: str = ""
    raw: dict = field(default_factory=dict)


class AkoueBlock(Block):
    """Auditory sensing organ. Phase A — audio file input,
    Whisper interpreter for speech → text.

    provides: hear
    requires: (none; uses external adapters with optional imports)

    Invocation is caretaker-authorized per D-049 safety guidance.
    """

    name = "akoue"
    provides = [
        Port("hear", description="Acquire and transcribe an audio source"),
    ]
    requires = []

    def __init__(self, whisper_model: str = "base",
                 cache_dir: str = ""):
        super().__init__()
        self._whisper_model = whisper_model
        self._cache_dir = cache_dir

    def on_attach(self):
        if not self._cache_dir and self._config:
            habitat = self._config.get("habitat_dir", "")
            if habitat:
                self._cache_dir = str(Path(habitat) / "akoue_cache")

    # --- Provides: hear ---

    def handle_hear(self, source: str, purpose: str = "") -> AkoueResult:
        """Sense an audio source.

        Args:
            source: path to an audio file (wav, mp3, m4a, flac, ogg).
                Live-mic capture is Phase B (not available yet).
            purpose: optional short string describing why the creature
                is listening. Shapes interpreter prompt hints
                (Whisper has a `initial_prompt` parameter).

        Returns:
            AkoueResult with transcription or error.
        """
        t0 = time.perf_counter()
        try:
            # --- Acquisition ---
            acquired_path = self._acquire(source)
            if acquired_path is None:
                return AkoueResult(
                    transcription="",
                    source=source,
                    channel="logos_fallback",
                    error=f"acquisition failed for {source!r}",
                    latency_ms=(time.perf_counter() - t0) * 1000,
                )

            # --- Interpretation (Phase A: always Whisper) ---
            transcription, interpreter_meta = self._interpret(
                acquired_path, purpose=purpose
            )
            elapsed = (time.perf_counter() - t0) * 1000

            if not transcription and interpreter_meta.get("error"):
                return AkoueResult(
                    transcription="",
                    source=source,
                    channel="logos_fallback",
                    interpreter=interpreter_meta.get("model", ""),
                    acquired_path=str(acquired_path),
                    error=interpreter_meta["error"],
                    latency_ms=elapsed,
                    raw=interpreter_meta,
                )

            result = AkoueResult(
                transcription=transcription,
                source=source,
                channel="logos_fallback",
                interpreter=interpreter_meta.get("model", self._whisper_model),
                acquired_path=str(acquired_path),
                duration_s=interpreter_meta.get("duration_s", 0.0),
                latency_ms=elapsed,
                raw=interpreter_meta,
            )

            # Emit trace span
            try:
                from ludex.core import trace as _tr
                _tr.emit_akoue_invoked(
                    self._organism,
                    source=source,
                    channel="logos_fallback",
                    purpose=purpose,
                    transcription_len=len(transcription),
                    duration_s=interpreter_meta.get("duration_s", 0.0),
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
                        source_kind="akoue",
                        content=transcription,
                        source=source,
                        purpose=purpose,
                        channel="logos_fallback",
                        field_name=_tr.current_field(),
                        metadata={
                            "interpreter": interpreter_meta.get("model", ""),
                            "latency_ms": round(elapsed, 1),
                            "duration_s": interpreter_meta.get("duration_s", 0.0),
                            "acquired_path": str(acquired_path),
                        },
                    ),
                )
            except Exception as e:
                logger.debug(f"akoue consolidation skipped: {e}")

            return result

        except Exception as e:
            logger.warning(f"Akoué.hear({source!r}) failed: {e}")
            return AkoueResult(
                transcription="",
                source=source,
                channel="logos_fallback",
                error=str(e),
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

    # --- Acquisition router ---

    def _acquire(self, source: str) -> Optional[Path]:
        if not source:
            return None
        s = source.strip()
        # Phase A: file paths only. URLs / streams / live-mic are Phase B.
        return self._acquire_file(s)

    def _acquire_file(self, path_str: str) -> Optional[Path]:
        from ludex.blocks.akoue_adapters.file_source import acquire_file
        return acquire_file(path_str)

    # --- Interpretation ---

    def _interpret(self, audio_path: Path, purpose: str = "") -> tuple[str, dict]:
        """Always Whisper in Phase A."""
        from ludex.blocks.akoue_adapters.whisper_interpreter import interpret
        return interpret(audio_path, purpose=purpose,
                         model=self._whisper_model)
