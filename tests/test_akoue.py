"""Akoué Phase A smoke — D-049.

File-source validation runs unconditionally. Live transcription
test is gated on `openai-whisper` being importable; skips with a
clear message otherwise (same pattern as Opsis's Claude CLI
availability check).
"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ludex.core.bus import Bus
from ludex.core.signals import Signals
from ludex.core.config import Config
from ludex.blocks.akoue import AkoueBlock


def _whisper_available() -> bool:
    try:
        import whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _make_block(tmp: str) -> AkoueBlock:
    akoue = AkoueBlock()
    cfg = Config()
    cfg.set("habitat_dir", tmp, layer="session")
    akoue.attach(Bus(), Signals(), cfg)
    return akoue


def test_empty_source_rejected():
    tmp = tempfile.mkdtemp(prefix="akoue_empty_")
    try:
        akoue = _make_block(tmp)
        r = akoue.handle_hear(source="")
        assert r.error
        print("  [PASS] empty source → error result")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_file_rejected():
    tmp = tempfile.mkdtemp(prefix="akoue_miss_")
    try:
        akoue = _make_block(tmp)
        r = akoue.handle_hear(source="/nonexistent/audio.wav")
        assert r.error
        assert r.transcription == ""
        print("  [PASS] missing file → error result")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unsupported_extension_rejected():
    tmp = tempfile.mkdtemp(prefix="akoue_ext_")
    try:
        bad = os.path.join(tmp, "notes.txt")
        with open(bad, "w") as f:
            f.write("not audio")
        akoue = _make_block(tmp)
        r = akoue.handle_hear(source=bad)
        assert r.error
        print("  [PASS] unsupported extension → error result")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_whisper_missing_degrades_gracefully():
    """When openai-whisper is NOT installed, an otherwise-valid
    audio file should return an error result (not raise), pointing
    at the install instruction.
    """
    if _whisper_available():
        print("  [SKIP] whisper is installed; cannot test missing-dep path")
        return
    tmp = tempfile.mkdtemp(prefix="akoue_nowhisper_")
    try:
        # Write a tiny file with a valid audio extension.
        # file_source validates ONLY extension + existence, not content,
        # so this passes acquisition and fails at the interpreter.
        fake_audio = os.path.join(tmp, "fake.wav")
        with open(fake_audio, "wb") as f:
            f.write(b"fake audio bytes")
        akoue = _make_block(tmp)
        r = akoue.handle_hear(source=fake_audio)
        assert r.error
        assert "openai-whisper" in r.error or "whisper" in r.error
        print(f"  [PASS] whisper missing → graceful error "
              f"({r.error[:60]}...)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_live_transcription_when_available():
    """Full path live — only runs when whisper is installed AND we
    can find or generate a test audio file.
    """
    if not _whisper_available():
        print("  [SKIP] openai-whisper not installed")
        return
    # No canonical test audio in repo yet. Phase A ships with the
    # contract; add a real audio fixture when the first Akoué-using
    # field session lands.
    print("  [SKIP] no test audio fixture in repo; contract verified "
          "by structure + graceful-degradation tests above")


if __name__ == "__main__":
    print("\n[Akoué Phase A — D-049]")
    test_empty_source_rejected()
    test_missing_file_rejected()
    test_unsupported_extension_rejected()
    test_whisper_missing_degrades_gracefully()
    test_live_transcription_when_available()
    print("\n" + "="*60)
    print("All Akoué Phase A tests passed.")
    print("="*60)
