"""Akoué interpreter — Whisper speech-to-text (D-049 Phase A).

Uses `openai-whisper` (Python package) when available. Returns a
clear error when not installed — graceful degradation matches
Opsis's Playwright url_source pattern.

Install (when actually using):
    pip install openai-whisper
    # or: pip install faster-whisper  (requires code swap below)

Model sizes (from whisper README):
    tiny   ~75MB   ~32x realtime
    base   ~150MB  ~16x realtime  ← Phase A default
    small  ~500MB  ~6x
    medium ~1.5GB  ~2x
    large  ~3GB    1x

Phase C considerations (per D-049): non-speech audio (music,
ambient) description is not Whisper's job — it falls back to
"non-speech audio, N seconds" placeholder or defers to an
audio-native brain via native channel.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


# Module-level cache so we load a whisper model once per process,
# not per call. Swapped on model-size change.
_loaded_model = None
_loaded_model_name: str = ""


def _load_model(model_name: str):
    """Lazy-load a whisper model. Re-loads if model name changes.
    Returns the loaded model, or None if whisper is not installed.
    """
    global _loaded_model, _loaded_model_name
    if _loaded_model is not None and _loaded_model_name == model_name:
        return _loaded_model
    try:
        import whisper  # type: ignore
    except ImportError as e:
        logger.warning(
            f"whisper_interpreter: openai-whisper not installed "
            f"(pip install openai-whisper): {e}"
        )
        return None
    try:
        _loaded_model = whisper.load_model(model_name)
        _loaded_model_name = model_name
        return _loaded_model
    except Exception as e:
        logger.warning(f"whisper_interpreter: failed to load {model_name!r}: {e}")
        return None


def interpret(
    audio_path: Path,
    purpose: str = "",
    model: str = "base",
) -> tuple[str, dict]:
    """Transcribe an audio file. Returns (transcription, meta).

    Meta: model, duration_s, language (auto-detected), elapsed_ms,
    error (if any).

    On missing dep or load failure, transcription is "" and meta
    includes an `error` field.
    """
    whisper_model = _load_model(model)
    if whisper_model is None:
        return "", {
            "model": model,
            "error": "openai-whisper not installed "
                     "(pip install openai-whisper)",
        }

    t0 = time.perf_counter()
    try:
        # initial_prompt lets the creature's purpose shape domain hints.
        # Whisper uses it to bias vocabulary selection.
        result = whisper_model.transcribe(
            str(audio_path),
            initial_prompt=purpose or None,
            verbose=False,
        )
    except Exception as e:
        logger.warning(f"whisper_interpreter: transcribe failed: {e}")
        return "", {"model": model, "error": f"transcribe failed: {e}"}

    elapsed_ms = (time.perf_counter() - t0) * 1000
    text = (result.get("text") or "").strip()
    meta = {
        "model": model,
        "duration_s": _probe_duration(result),
        "language": result.get("language", ""),
        "elapsed_ms": elapsed_ms,
        "segments": len(result.get("segments", []) or []),
    }
    return text, meta


def _probe_duration(whisper_result: dict) -> float:
    """Best-effort duration from whisper output.

    Whisper's `transcribe` result has `segments` with end-timestamps;
    take the last one as total duration. 0.0 if unavailable.
    """
    segments = whisper_result.get("segments") or []
    if not segments:
        return 0.0
    last = segments[-1]
    return float(last.get("end", 0.0) or 0.0)
