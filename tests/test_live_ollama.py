"""
Live Ollama Test — 진짜 공기를 마시다

Mock이 아닌 실제 Ollama에 연결하여 유기체가 작동하는지 검증.
Ollama가 실행 중이어야 함 (http://127.0.0.1:11434)
"""

import sys
import os
import urllib.request
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism import Organism
from ludex.blocks.provider import ProviderBlock
from ludex.blocks.resilience import ResilienceBlock
from ludex.blocks.engine import EngineBlock
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.registry import RegistryBlock, ToolDefinition
from ludex.blocks.hooks import HooksBlock


_REQUIRED_MODELS = ("exaone3.5:7.8b",)


def _ollama_models_available() -> tuple[bool, str]:
    """Probe the canonical Ollama dev endpoint and confirm the test
    models are pulled. Returns (ok, reason) — `reason` is the skip
    explanation when ok=False. The daemon being reachable but the
    model being absent (the common case on a Mac dev machine that
    only pulled some of the cohort's models) is treated the same
    as the daemon being unreachable for skip purposes: pytest unit
    runs should not depend on external model state."""
    try:
        import json
        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags", timeout=2
        ) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return False, (
            f"Live Ollama daemon at http://127.0.0.1:11434 not reachable: {e}"
        )
    available = {m.get("name") for m in (data.get("models") or [])}
    missing = [m for m in _REQUIRED_MODELS if m not in available]
    if missing:
        return False, (
            f"Required Ollama models not pulled on this host: "
            f"{missing} (available: {sorted(available)}). "
            "Run `ollama pull <model>` to enable live tests."
        )
    return True, ""


_ok, _skip_reason = _ollama_models_available()
pytestmark = pytest.mark.skipif(not _ok, reason=_skip_reason or "ok")


def test_live_health_check():
    """Ollama 연결 확인"""
    print("\n  --- Health Check ---")
    org = Organism(
        name="health-check",
        blocks=[ProviderBlock(provider="ollama", model="exaone3.5:7.8b")],
    )
    provider = org.get_block("provider")
    health = provider.handle_health_check()
    print(f"    Status: {health['status']}")
    print(f"    Models: {health.get('models', [])[:5]}...")
    assert health["status"] == "healthy"
    print("  [PASS] Ollama is alive")


def test_live_simple_call():
    """단순 LLM 호출"""
    print("\n  --- Simple Call ---")
    org = Organism(
        name="simple-call",
        blocks=[
            ProviderBlock(provider="ollama", model="exaone3.5:7.8b"),
            ResilienceBlock(max_retries=2),
        ],
    )
    provider = org.get_block("provider")
    result = provider.handle_llm_call("Say hello in one sentence.")

    from ludex.blocks.provider import LLMResponse
    assert isinstance(result, LLMResponse), f"Expected LLMResponse, got {type(result)}: {result}"
    print(f"    Model: {result.model}")
    print(f"    Response: {result.content[:100]}...")
    print(f"    Tokens: in={result.tokens_in}, out={result.tokens_out}")
    print(f"    Latency: {result.latency_ms:.0f}ms")
    assert len(result.content) > 0
    print("  [PASS] Simple call works")


def test_live_engine_conversation():
    """Engine을 통한 멀티턴 대화"""
    print("\n  --- Engine Conversation (3 turns) ---")
    org = Organism(
        name="live-conversation",
        blocks=[
            ProviderBlock(provider="ollama", model="exaone3.5:7.8b"),
            ResilienceBlock(max_retries=2, initial_delay_ms=500),
            EngineBlock(max_turns=5, token_budget=2000, system_prompt="You are a helpful assistant. Answer briefly."),
            TrackingBlock(experiment_name="live-test"),
        ],
        config={"model": "exaone3.5:7.8b"},
    )

    engine = org.get_block("engine")
    tracking = org.get_block("tracking")

    prompts = [
        "What is 2+2? Answer in one word.",
        "What color is the sky? One word.",
        "Name one planet. One word.",
    ]

    for i, prompt in enumerate(prompts):
        print(f"    Turn {i+1}: {prompt}")
        result = engine.handle_submit(prompt)
        print(f"      Response: {result.response[:80]}...")
        print(f"      Tokens: in={result.tokens_in}, out={result.tokens_out}, Latency: {result.latency_ms:.0f}ms")
        assert result.stop_reason == "completed", f"Turn {i+1} failed: {result.error}"

    # 보고서
    report = tracking.handle_get_report()
    print(f"\n    Report: {report['total_turns']} turns, {report['total_tokens']} tokens, "
          f"{report['avg_latency_ms']:.0f}ms avg latency")

    # 생체 신호
    vitals = org.measure_vitals()
    print(f"    Vitals: age={vitals.temporal.age_seconds:.1f}s, "
          f"tokens/turn={vitals.tokens_per_turn:.0f}, "
          f"errors={vitals.error_rate:.0%}")

    assert report["total_turns"] == 3
    assert report["error_rate"] == 0.0
    print("  [PASS] Engine conversation works")


def test_live_with_hooks():
    """Hooks로 프롬프트 강화"""
    print("\n  --- Hooks Enhancement ---")
    hooks = HooksBlock()
    org = Organism(
        name="live-hooks",
        blocks=[
            ProviderBlock(provider="ollama", model="exaone3.5:7.8b"),
            ResilienceBlock(max_retries=2, initial_delay_ms=500),
            EngineBlock(max_turns=3, token_budget=2000,
                       system_prompt="You are a chess expert. Answer briefly."),
            hooks,
        ],
        config={"model": "exaone3.5:7.8b"},
    )

    # 훅: 턴 후 응답 길이 기록
    response_lengths = []
    hooks.after_turn("measure_response",
        lambda **kw: response_lengths.append(kw.get("turn_number", 0)))

    engine = org.get_block("engine")
    result = engine.handle_submit("What is the best first move in chess? One sentence.")
    print(f"    Response: {result.response[:100]}...")
    assert result.stop_reason == "completed"
    assert len(response_lengths) >= 1
    print("  [PASS] Hooks work with live LLM")


def test_live_different_model():
    """다른 모델로 전환"""
    print("\n  --- Model Switch ---")
    org = Organism(
        name="model-switch",
        blocks=[
            ProviderBlock(provider="ollama", model="mistral:7b"),
            EngineBlock(max_turns=3, token_budget=2000),
        ],
        config={"model": "mistral:7b"},
    )

    engine = org.get_block("engine")
    result = engine.handle_submit("Say hello in French. One sentence only.")
    print(f"    Mistral response: {result.response[:80]}...")
    assert result.stop_reason == "completed"

    # Config로 모델 전환
    org.config.set("model", "exaone3.5:7.8b")
    result2 = engine.handle_submit("Say hello in Korean. One sentence only.")
    print(f"    Exaone response: {result2.response[:80]}...")
    assert result2.stop_reason == "completed"

    print("  [PASS] Model switch via Config works")


if __name__ == "__main__":
    print("=" * 60)
    print("Ludex Live Ollama Tests — First Real Breath")
    print("=" * 60)

    test_live_health_check()
    test_live_simple_call()
    test_live_engine_conversation()
    test_live_with_hooks()
    test_live_different_model()

    print("\n" + "=" * 60)
    print("All live tests passed! The organism breathes real air.")
    print("=" * 60)
