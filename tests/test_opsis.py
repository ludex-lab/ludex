"""Opsis Phase A smoke — D-048.

Requires Claude CLI on PATH. Skips gracefully otherwise. Live
LLM call (~15-30s depending on network). Not a pure unit test;
this is an integration smoke.
"""
import sys, os, tempfile, shutil, shutil as _sh
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ludex.core.bus import Bus
from ludex.core.signals import Signals
from ludex.core.config import Config
from ludex.blocks.opsis import OpsisBlock


TEST_IMAGE = os.path.join(
    os.path.dirname(__file__), "..",
    "research/emotion_benchmark/emotion_pilot_output/emotion_pca.png",
)


def _claude_cli_available() -> bool:
    return _sh.which("claude") is not None


def test_file_source_rejects_missing():
    """Non-existent file returns an error result, not an exception."""
    tmp = tempfile.mkdtemp(prefix="opsis_miss_")
    try:
        opsis = OpsisBlock()
        cfg = Config()
        cfg.set("habitat_dir", tmp, layer="session")
        opsis.attach(Bus(), Signals(), cfg)
        r = opsis.handle_see(source="/nonexistent/path.png")
        assert r.error, "expected error"
        assert r.description == ""
        print("  [PASS] missing file → error result")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_file_source_rejects_unsupported_ext():
    """A path with wrong extension is rejected at acquisition."""
    tmp = tempfile.mkdtemp(prefix="opsis_ext_")
    try:
        # Create a text file, name it something that isn't an image ext
        bad = os.path.join(tmp, "notes.txt")
        with open(bad, "w") as f:
            f.write("not an image")
        opsis = OpsisBlock()
        cfg = Config()
        cfg.set("habitat_dir", tmp, layer="session")
        opsis.attach(Bus(), Signals(), cfg)
        r = opsis.handle_see(source=bad)
        assert r.error
        print("  [PASS] unsupported extension → error result")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_source_rejected():
    tmp = tempfile.mkdtemp(prefix="opsis_empty_")
    try:
        opsis = OpsisBlock()
        cfg = Config()
        cfg.set("habitat_dir", tmp, layer="session")
        opsis.attach(Bus(), Signals(), cfg)
        r = opsis.handle_see(source="")
        assert r.error
        print("  [PASS] empty source → error result")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_live_interpretation():
    """Full path: file → Claude CLI interpreter → description."""
    if not _claude_cli_available():
        print("  [SKIP] claude CLI not on PATH")
        return
    if not os.path.exists(TEST_IMAGE):
        print(f"  [SKIP] test image missing: {TEST_IMAGE}")
        return

    tmp = tempfile.mkdtemp(prefix="opsis_live_")
    try:
        opsis = OpsisBlock()
        cfg = Config()
        cfg.set("habitat_dir", tmp, layer="session")
        opsis.attach(Bus(), Signals(), cfg)

        r = opsis.handle_see(
            source=TEST_IMAGE,
            purpose="check whether emotion PCA separated valence cleanly",
        )
        assert not r.error, f"unexpected error: {r.error}"
        assert r.channel == "logos_fallback"
        assert r.interpreter == "claude-opus-4-6"
        assert len(r.description) > 50, (
            f"description too short ({len(r.description)})"
        )
        # Description should reference something identifiable about the
        # image — this is a PCA scatter, so "PC" or "scatter" or "PCA"
        # should appear. Loose check.
        lowered = r.description.lower()
        assert any(kw in lowered for kw in ("pc1", "pca", "scatter", "emotion")), (
            f"description missing expected keywords: {r.description[:200]!r}"
        )
        print(f"  [PASS] live interpretation "
              f"({len(r.description)}ch, {r.latency_ms:.0f}ms)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("\n[Opsis Phase A — D-048]")
    test_empty_source_rejected()
    test_file_source_rejects_missing()
    test_file_source_rejects_unsupported_ext()
    test_live_interpretation()
    print("\n" + "="*60)
    print("All Opsis tests passed.")
    print("="*60)
