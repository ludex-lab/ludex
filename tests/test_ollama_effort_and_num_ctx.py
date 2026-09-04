"""Ollama wire contract — reasoning effort and KV-cache size reach the model.

Requested by 이음 (lab:ludex-village) 2026-08-26, envelope hub-ops
from-ludex-village/002, with measurements from an M3 Ultra studio host:

  * `brain.effort` never reached ollama at all — `"think": false` was
    hardcoded — so a creature's reasoning tier stopped at the config file.
    With think=false a 4B model answered a permutation problem in 0.17s and
    got it wrong (4 rendered as 6); with think="low" it spent the whole
    1,000-token output budget reasoning and returned an empty final answer.
    Both are real failure modes and neither is observable until effort is
    actually wired.
  * `num_ctx` had no path from creature to server. The same 4B model loaded
    at 4.2 GB with num_ctx=32768 and at 12 GB with the server's 262144
    default — 7.8 GB of habitat from one unsettable field.

The compatibility policy for an unset field is pinned here on purpose: a
creature born before this wiring must behave exactly as it did.
"""

from __future__ import annotations

import json
import logging

import pytest

from ludex.blocks.adapters.ollama import OllamaAdapter, think_value
from ludex.blocks.adapters.base import AdapterResponse
from ludex.blocks.provider import LLMError, ProviderBlock
from ludex.core.organism_config import (
    DEFAULT_OLLAMA_NUM_CTX, EffortContractError, OrganismConfig,
    effort_contract,
)


class _Capture:
    """Stands in for the HTTP layer and keeps the body that would go out."""

    def __init__(self, result=None):
        self.body = None
        self._result = result or {"message": {"content": "ok"}}

    def __call__(self, url, data=None, method="GET"):
        self.body = json.loads(data.decode("utf-8"))
        return self._result


def _call(monkeypatch, **kwargs):
    cap = _Capture(kwargs.pop("_result", None))
    a = OllamaAdapter()
    monkeypatch.setattr(a, "_request", cap)
    a.call(kwargs.pop("model", "qwen3.8:27b"), prompt="hi", **kwargs)
    return cap.body


# ── effort → think ──────────────────────────────────────────────────────

def test_effort_reaches_the_wire(monkeypatch):
    assert _call(monkeypatch, effort="low")["think"] == "low"


def test_regular_calls_use_the_streaming_transport(monkeypatch):
    """Long local generations must expose progress before their deadline."""
    assert _call(monkeypatch)["stream"] is True


def test_empty_effort_keeps_the_historical_non_thinking_default(monkeypatch):
    """The compatibility pin: silence must not become thinking."""
    assert _call(monkeypatch, effort="")["think"] is False


def test_unserved_tier_is_dropped_loudly_not_sent(monkeypatch, caplog):
    """qwen3.8 serves low/medium/xhigh — Ludex's common scale also has high."""
    with caplog.at_level(logging.WARNING):
        body = _call(monkeypatch, model="qwen3.8:27b", effort="high")
    assert body["think"] is False, "a tier the template rejects is a bad wire value"
    assert "does not serve" in caplog.text, "a silent downgrade would hide it"


def test_unknown_model_defers_to_the_server(monkeypatch):
    """The tier table refuses only where a refusal is grounded."""
    assert _call(monkeypatch, model="llama3.1:8b", effort="high")["think"] == "high"


def test_think_value_is_pure():
    assert think_value("qwen3.8:27b", "xhigh") == "xhigh"
    assert think_value("qwen3.8:27b", "max") is False
    assert think_value("qwen3.8:27b", "") is False


# ── num_ctx ─────────────────────────────────────────────────────────────

def test_num_ctx_reaches_options(monkeypatch):
    assert _call(monkeypatch, num_ctx=32768)["options"]["num_ctx"] == 32768


def test_unset_num_ctx_sends_nothing(monkeypatch):
    """Compatibility pin: an existing creature keeps the server's default."""
    assert "num_ctx" not in _call(monkeypatch)["options"]


def test_new_ollama_births_carry_an_explicit_window():
    cfg = OrganismConfig.from_preset("full", name="T", provider="ollama",
                                     model="qwen3.8:27b")
    assert cfg.brain["num_ctx"] == DEFAULT_OLLAMA_NUM_CTX


def test_build_forwards_the_generation_envelope(monkeypatch):
    """A creature-level cap is real only if build() passes it to Provider."""
    monkeypatch.setattr(OrganismConfig, "_wire_function_calling",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(OrganismConfig, "_maybe_probe_brain_capabilities",
                        lambda *args, **kwargs: None)
    cfg = OrganismConfig.from_preset("minimal", name="T", provider="ollama",
                                     model="qwen3.8:27b")
    cfg.brain.update({"max_tokens": 1024, "temperature": 0.25,
                      "timeout_ms": 180000})
    org = cfg.build()
    provider = org.get_block("provider")
    assert provider._init_config["max_tokens"] == 1024
    assert provider._init_config["temperature"] == 0.25
    assert provider._init_config["timeout_ms"] == 180000


def test_non_ollama_births_are_untouched():
    cfg = OrganismConfig.from_preset("full", name="T", provider="claude_cli",
                                     model="claude-sonnet-5")
    assert "num_ctx" not in cfg.brain


# ── save-time contract ──────────────────────────────────────────────────

def test_contract_knows_the_served_tiers():
    assert effort_contract("ollama", "qwen3.8:27b") == {"low", "medium", "xhigh"}
    assert effort_contract("ollama", "llama3.1:8b") is None


def test_unserved_tier_cannot_be_saved(tmp_path):
    """A config error should be impossible to save, not found in a battery."""
    home = tmp_path / "T"
    home.mkdir()
    cfg = OrganismConfig.from_preset("full", name="T", provider="ollama",
                                     model="qwen3.8:27b")
    cfg.brain["effort"] = "high"
    cfg.habitat.home_dir = str(home)
    with pytest.raises(EffortContractError):
        cfg.save()


# ── thinking trace ──────────────────────────────────────────────────────

def test_thinking_never_mixes_into_content(monkeypatch):
    body_result = {"message": {"content": "the answer is 4",
                               "thinking": "let me enumerate the permutations"}}
    cap = _Capture(body_result)
    a = OllamaAdapter()
    monkeypatch.setattr(a, "_request", cap)
    r = a.call("qwen3.8:27b", prompt="hi", effort="low")
    assert r.content == "the answer is 4"
    assert "permutations" not in r.content
    assert r.raw["message"]["thinking"], "the trace is preserved in raw"


def test_budget_spent_on_reasoning_is_named_not_silent(monkeypatch, caplog):
    """이음's second measurement: think=low ate 1,000 tokens, answer empty."""
    cap = _Capture({"message": {"content": "", "thinking": "x" * 400}})
    a = OllamaAdapter()
    monkeypatch.setattr(a, "_request", cap)
    with caplog.at_level(logging.WARNING):
        r = a.call("qwen3.8:27b", prompt="hi", effort="low", max_tokens=1000)
    assert r.content == ""
    assert "output budget on reasoning" in caplog.text, \
        "an empty answer with a full trace must not look like a dead brain"


def test_thinking_plus_tool_call_is_not_mislabeled_as_empty_answer(
        monkeypatch, caplog):
    cap = _Capture({
        "message": {
            "content": "",
            "thinking": "I should inspect the body's vitals.",
            "tool_calls": [{
                "function": {"name": "ludex_vitals", "arguments": {}},
            }],
        },
    })
    adapter = OllamaAdapter()
    monkeypatch.setattr(adapter, "_request", cap)

    with caplog.at_level(logging.WARNING):
        result = adapter.call("qwen3.8:27b", prompt="check", effort="low")

    assert result.tool_calls[0]["function"]["name"] == "ludex_vitals"
    assert "spent its output budget" not in caplog.text
    assert "timed out with partial reasoning" not in caplog.text


def test_timeout_with_reasoning_is_not_mislabeled_as_budget_exhaustion(
        monkeypatch, caplog):
    cap = _Capture({
        "message": {"content": "", "thinking": "x" * 40},
        "timeout": True,
        "elapsed_ms": 5001,
    })
    a = OllamaAdapter()
    monkeypatch.setattr(a, "_request", cap)
    with caplog.at_level(logging.WARNING):
        a.call("qwen3.8:27b", prompt="hi", effort="low", max_tokens=512)
    assert "timed out with partial reasoning" in caplog.text
    assert "spent its output budget" not in caplog.text


def test_stream_timeout_preserves_partial_work_without_fake_token_counts():
    raw = OllamaAdapter._aggregate_json_stream([
        {"message": {"role": "assistant", "thinking": "생각 ", "content": ""},
         "done": False},
        {"message": {"role": "assistant", "thinking": "중", "content": "답"},
         "done": False},
    ], timed_out=True, elapsed_ms=180001)
    assert raw["timeout"] is True
    assert raw["partial"] is True
    assert raw["message"]["thinking"] == "생각 중"
    assert raw["message"]["content"] == "답"
    assert raw["stream_chunks"] == 2
    assert "eval_count" not in raw, "partial chunks are not measured token usage"
    assert raw["partial_usage"] == {
        "token_counts_available": False,
        "stream_chunks": 2,
        "thinking_chars": 4,
        "content_chars": 1,
    }


def test_stream_terminal_chunk_keeps_exact_usage():
    raw = OllamaAdapter._aggregate_json_stream([
        {"message": {"role": "assistant", "content": "완"}, "done": False},
        {"message": {"role": "assistant", "content": "료"}, "done": True,
         "done_reason": "stop", "prompt_eval_count": 12, "eval_count": 34},
    ], timed_out=False, elapsed_ms=1000)
    assert raw["message"]["content"] == "완료"
    assert raw["partial"] is False
    assert raw["prompt_eval_count"] == 12
    assert raw["eval_count"] == 34
    assert raw["partial_usage"]["token_counts_available"] is True


def test_stream_timeout_becomes_typed_error_with_partial_kept():
    class _PartialAdapter:
        provider_name = "ollama"

        def call(self, **kwargs):
            return AdapterResponse(
                content="부분 답",
                raw={"timeout": True, "done_reason": "timeout",
                     "elapsed_ms": 180001, "stream_chunks": 17,
                     "message": {"content": "부분 답", "thinking": "생각"}},
            )

    provider = ProviderBlock(provider="ollama", model="qwen3.8:27b")
    provider._adapter = _PartialAdapter()
    result = provider.handle_llm_call(prompt="hi")
    assert isinstance(result, LLMError)
    assert result.error_type == "timeout"
    assert result.partial_content == "부분 답"
    assert result.raw["stream_chunks"] == 17


def test_length_stop_is_non_retryable_and_kept_as_partial():
    class _LengthAdapter:
        provider_name = "ollama"

        def call(self, **kwargs):
            return AdapterResponse(
                content="완성되기 전 잘린 답",
                tokens_in=10,
                tokens_out=1024,
                raw={"done": True, "done_reason": "length",
                     "elapsed_ms": 76000, "eval_count": 1024,
                     "message": {"content": "완성되기 전 잘린 답"}},
            )

    provider = ProviderBlock(provider="ollama", model="qwen3.8:27b")
    provider._adapter = _LengthAdapter()
    result = provider.handle_llm_call(prompt="hi")
    assert isinstance(result, LLMError)
    assert result.error_type == "output_limit"
    assert result.retryable is False
    assert result.partial_content == "완성되기 전 잘린 답"


def test_timeout_classifier_uses_exception_type_and_timed_out_spelling():
    import socket
    import urllib.error

    assert ProviderBlock._classify_error(TimeoutError("timed out")) == "timeout"
    assert ProviderBlock._classify_error(socket.timeout("timed out")) == "timeout"
    wrapped = urllib.error.URLError(socket.timeout("timed out"))
    assert ProviderBlock._classify_error(wrapped) == "timeout"
    assert ProviderBlock._classify_error(Exception("operation timed out")) == "timeout"


# ── FC probe: a body's first contact with its brain ─────────────────────

class _ProbeCapture:
    def __init__(self):
        self.bodies = []

    def __call__(self, url, data=None, method="GET"):
        self.bodies.append(json.loads(data.decode("utf-8")))
        return {"message": {"content": "hi"}}


def test_fc_probe_carries_the_configured_context(monkeypatch):
    """이음, 2026-08-26: supports_tools alone left ollama ps at 262144.

    The probe builds its own request body, which is how it kept its own
    blind spot after call() was fixed — and it often runs BEFORE any
    configured call, so whatever it loads is what the habitat pays first.
    """
    cap = _ProbeCapture()
    a = OllamaAdapter()
    monkeypatch.setattr(a, "_request", cap)
    a.supports_tools("qwen3.8:27b", num_ctx=32768)
    assert cap.bodies[0]["options"]["num_ctx"] == 32768


def test_fc_probe_without_config_sends_no_options(monkeypatch):
    cap = _ProbeCapture()
    a = OllamaAdapter()
    monkeypatch.setattr(a, "_request", cap)
    a.supports_tools("qwen3.8:27b")
    assert "options" not in cap.bodies[0]


def test_fc_probe_never_thinks(monkeypatch):
    """Asking whether a capability exists must not cost reasoning — and a
    thinking model can spend its whole budget and return nothing, which this
    probe would read as failure."""
    cap = _ProbeCapture()
    a = OllamaAdapter()
    monkeypatch.setattr(a, "_request", cap)
    a.supports_tools("qwen3.8:27b", num_ctx=32768)
    assert cap.bodies[0]["think"] is False


def test_cached_verdict_makes_no_second_probe(tmp_path, monkeypatch):
    """The 08-24 cache still holds: one probe per brain identity, not per build."""
    import yaml
    calls = []

    def _fake(self, model, num_ctx=None):
        calls.append((model, num_ctx))
        return False

    monkeypatch.setattr(OllamaAdapter, "supports_tools", _fake)
    home = tmp_path / "T"
    (home / "memory").mkdir(parents=True)
    (home / "ludex.yaml").write_text(yaml.safe_dump({
        "name": "T",
        "brain": {"provider": "ollama", "model": "qwen3.8:27b", "num_ctx": 32768},
        "brain_capabilities": ["narrative"],
        "capability_probed_brain": "ollama:qwen3.8:27b",
        "capability_probed_at": 1.0,
        "organs": {"engine": {"enabled": True, "required": True},
                   "memory": {"enabled": True}},
        "habitat": {"mode": "local", "home_dir": str(home),
                    "persistent": True, "origin": ""},
        "born_at": 1000.0, "session_count": 1,
    }, sort_keys=False), encoding="utf-8")

    OrganismConfig.load(str(home)).build()
    assert calls == [("qwen3.8:27b", 32768)], "the creature's own window, once"
    OrganismConfig.load(str(home)).build()
    assert len(calls) == 1, "a cached verdict must not re-probe"


# ── birth capability probe: the last step of build() ────────────────────

def test_birth_probe_carries_the_configured_context(monkeypatch):
    """이음, 2026-08-26: a 27B birth landed at 18 GB / context 262144 while
    the creature's own ludex.yaml asked for 32768. The capability probe is
    the LAST step of build(), so whatever it loads is what stays resident."""
    from ludex.core import birth_probe
    seen = {}

    class _A:
        def call(self, model, prompt="", effort="", num_ctx=None):
            seen.update(model=model, effort=effort, num_ctx=num_ctx)
            class R: content = '{"ok": true}'
            return R()

    monkeypatch.setattr(birth_probe, "_build_adapter", lambda *a, **k: _A())
    birth_probe.probe_brain_capabilities("ollama", "qwen3.8:27b",
                                         effort="low", num_ctx=32768)
    assert seen["num_ctx"] == 32768
    assert seen["effort"] == "low", "effort must not regress while adding num_ctx"


def test_birth_probe_omits_unset_context(monkeypatch):
    from ludex.core import birth_probe
    seen = {}

    class _A:
        def call(self, model, prompt="", effort="", num_ctx=None):
            seen.update(num_ctx=num_ctx)
            class R: content = "hi"
            return R()

    monkeypatch.setattr(birth_probe, "_build_adapter", lambda *a, **k: _A())
    birth_probe.probe_brain_capabilities("ollama", "qwen3.8:27b")
    assert seen["num_ctx"] is None


def test_birth_probe_never_passes_context_to_an_adapter_without_it(monkeypatch):
    """CLI adapters take no num_ctx; passing it would be a TypeError that the
    caller's broad except would swallow into a capability-less creature."""
    from ludex.core import birth_probe

    class _CliLike:
        def call(self, model, prompt="", effort=""):
            class R: content = "hi"
            return R()

    monkeypatch.setattr(birth_probe, "_build_adapter", lambda *a, **k: _CliLike())
    snap = birth_probe.probe_brain_capabilities("claude_cli", "claude-sonnet-5",
                                                effort="high", num_ctx=32768)
    assert not snap.get("error"), snap.get("error")
    assert snap["narrative"] is True


def test_every_ollama_request_builder_accepts_a_context_window():
    """Structural guard: three paths built an ollama body and each was fixed
    only when someone measured its leak — call(), supports_tools(), and the
    birth probe, found on 08-26 in that order. A fourth builder must declare
    the window or fail here rather than in a habitat's memory."""
    import inspect
    from ludex.blocks.adapters import ollama as _o
    builders = [_o.OllamaAdapter.call, _o.OllamaAdapter.supports_tools]
    for fn in builders:
        assert "num_ctx" in inspect.signature(fn).parameters, fn.__name__
    from ludex.core.birth_probe import probe_brain_capabilities
    assert "num_ctx" in inspect.signature(probe_brain_capabilities).parameters
