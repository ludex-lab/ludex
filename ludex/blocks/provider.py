"""
Provider Block — 호흡기 (LLM API Communication)

LLM 프로바이더와의 통신을 담당하는 장기.
Adapter 패턴으로 Ollama, OpenAI, Anthropic 등을 통합.

Config를 단일 소스로 사용 — model, temperature 등은 항상 Config에서 읽음.

Reference:
- OC: extensions/ollama/ (NDJSON streaming, model discovery)
- CC: rust/crates/api/client.rs (SSE parser, retry)
"""

from __future__ import annotations

import time
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Optional

from ludex.core.block import Block
from ludex.core.port import Port
from ludex.core import trace as _tr
from ludex.blocks.adapters.base import BaseAdapter, AdapterResponse
from ludex.blocks.adapters.ollama import OllamaAdapter
from ludex.blocks.adapters.openai_compat import OpenAIAdapter, GeminiApiAdapter
from ludex.blocks.adapters.anthropic import AnthropicAdapter
from ludex.blocks.adapters.claude_cli import ClaudeCliAdapter
from ludex.blocks.adapters.claude_sdk import ClaudeSdkAdapter
from ludex.blocks.adapters.gemini_cli import GeminiCliAdapter
from ludex.blocks.adapters.agy_cli import AgyCliAdapter
from ludex.blocks.adapters.codex_cli import CodexCliAdapter
from ludex.blocks.adapters.grok_cli import GrokCliAdapter
from ludex.blocks.adapters.cursor_cli import CursorCliAdapter

logger = logging.getLogger(__name__)


# --- Provider Response Types ---

@dataclass(frozen=True)
class LLMResponse:
    """LLM 응답 결과"""
    content: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    tool_calls: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LLMError:
    """LLM 호출 에러"""
    error_type: str     # "connection", "timeout", "rate_limit", "model_error", "auth"
    message: str
    retryable: bool = True
    retry_after_ms: Optional[float] = None
    # A streaming adapter may have useful work before a failed terminal
    # boundary. Keep it out of the normal response surface so callers cannot
    # publish an incomplete artifact, while preserving it for diagnosis.
    partial_content: str = ""
    raw: dict = field(default_factory=dict)


# --- Adapter Registry ---

ADAPTER_REGISTRY: dict[str, type[BaseAdapter]] = {
    "ollama": OllamaAdapter,
    "openai": OpenAIAdapter,
    # OpenRouter = same OpenAI-compatible wire, different house. A separate
    # provider name (not base_url override on "openai") so the env keys never
    # collide: an openai creature reads OPENAI_API_KEY, an openrouter creature
    # reads OPENROUTER_API_KEY. First tenant: upstage/solar-pro4 (2026-08-22)
    # — the village's first Korean lineage, ~$0.05/month at village cadence.
    "openrouter": OpenAIAdapter,
    "gemini_api": GeminiApiAdapter,
    "anthropic": AnthropicAdapter,
    "claude_cli": ClaudeCliAdapter,
    "claude_sdk": ClaudeSdkAdapter,
    "gemini_cli": GeminiCliAdapter,
    "agy_cli": AgyCliAdapter,
    "codex_cli": CodexCliAdapter,
    "grok_cli": GrokCliAdapter,
    "cursor_cli": CursorCliAdapter,
}

DEFAULT_BASE_URLS: dict[str, str] = {
    "ollama": "http://127.0.0.1:11434",
    "openai": "https://api.openai.com",
    "openrouter": "https://openrouter.ai/api",   # adapter appends /v1/chat/completions
    "gemini_api": "https://generativelanguage.googleapis.com/v1beta/openai",
    "anthropic": "https://api.anthropic.com",
    # claude: single source of truth = the adapter's shutil.which resolver
    # (same lesson as agy_cli/cursor_cli below — the hardcoded "claude.cmd"
    # missed the native installer's claude.exe and silently emptied every
    # claude_cli ENGINE reply on a fresh host, 2026-09-02, Hearth/Quill).
    "claude_cli": __import__("ludex.blocks.adapters.claude_cli", fromlist=["_CLAUDE_CMD"])._CLAUDE_CMD,
    "claude_sdk": "claude_sdk",
    "gemini_cli": "gemini.cmd" if __import__("os").name == "nt" else "gemini",
    # agy: curl install ships agy.exe (no .cmd) — a hardcoded "agy.cmd" here
    # overrode the adapter's shutil.which resolution and broke the ENGINE path
    # while the (base_url-less) birth-probe path worked (2026-07-15, Wick
    # re-brain catch-up). Single source of truth: the adapter's resolver.
    "agy_cli": __import__("ludex.blocks.adapters.agy_cli", fromlist=["_AGY_CMD"])._AGY_CMD,
    "codex_cli": "codex.cmd" if __import__("os").name == "nt" else "codex",
    "grok_cli": "grok.cmd" if __import__("os").name == "nt" else "grok",
    # cursor: single source of truth = the adapter's shutil.which resolver
    # (same lesson as agy_cli above — don't override with a hardcoded name).
    # [0] because the Windows resolver returns [node.exe, index.js]; the
    # adapter re-expands it to the full argv when it sees its own default.
    "cursor_cli": __import__("ludex.blocks.adapters.cursor_cli", fromlist=["_CURSOR_CMD"])._CURSOR_CMD[0],
}


# --- Provider Block ---

class ProviderBlock(Block):
    """
    LLM 프로바이더 블록. Adapter 패턴으로 다양한 API 지원.

    provides: llm_call, health_check, list_models
    requires: (없음)

    모든 호출 시 Config에서 model, temperature 등을 읽음 (단일 소스 원칙).
    """

    name = "provider"
    provides = [
        Port("llm_call", description="Call LLM with prompt"),
        Port("health_check", description="Check provider health"),
        Port("list_models", description="List available models"),
    ]
    requires = []

    def __init__(
        self,
        provider: str = "ollama",
        model: str = "llama3.1:8b",
        base_url: str = "",
        api_key: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout_ms: int = 30000,
        cwd: str = "",
        effort: str = "",
        auth: str = "",
        num_ctx: Optional[int] = None,
        write_dirs: Optional[list] = None,
    ):
        super().__init__()

        # 이 값들은 Config에 주입되며, 이후에는 항상 Config에서 읽음
        self._init_config = {
            "provider": provider,
            "model": model,
            "base_url": base_url or DEFAULT_BASE_URLS.get(provider, "http://127.0.0.1:11434"),
            "api_key": api_key,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout_ms": timeout_ms,
            "cwd": cwd,
            "effort": effort,
            "auth": auth,
            "num_ctx": num_ctx,
            "write_dirs": list(write_dirs or []),
        }

        # Adapter 생성
        self._adapter: Optional[BaseAdapter] = None
        self._create_adapter(provider, self._init_config["base_url"], api_key,
                             timeout_ms, cwd, auth, self._init_config["write_dirs"])

    def _create_adapter(self, provider: str, base_url: str, api_key: str, timeout_ms: int, cwd: str = "", auth: str = "", write_dirs: Optional[list] = None):
        adapter_cls = ADAPTER_REGISTRY.get(provider)
        if adapter_cls:
            # Pass cwd to adapters that support it (claude_cli, claude_sdk, gemini_cli, agy_cli, codex_cli)
            kwargs = {"base_url": base_url, "api_key": api_key, "timeout_ms": timeout_ms}
            if provider in ("claude_cli", "claude_sdk", "gemini_cli", "agy_cli", "codex_cli", "grok_cli", "cursor_cli") and cwd:
                kwargs["cwd"] = cwd
            # brain.auth (subscription|api) — birth-time billing decision honored
            # by the 4 subprocess CLI adapters when building their child env.
            # claude_sdk is excluded: it's in-process (no subprocess env to strip)
            # and forwards **kwargs to BaseAdapter, which rejects an `auth` arg.
            if provider in ("claude_cli", "gemini_cli", "agy_cli", "codex_cli", "grok_cli", "cursor_cli"):
                kwargs["auth"] = auth
            # Ruling No. 3 write jurisdiction — claude_cli only, opt-in. Empty
            # by default; the village layer passes the desk/tasks dirs.
            if provider == "claude_cli" and write_dirs:
                kwargs["write_dirs"] = write_dirs
            self._adapter = adapter_cls(**kwargs)
        else:
            # 기본: OpenAI-compatible
            self._adapter = OpenAIAdapter(base_url=base_url, api_key=api_key, timeout_ms=timeout_ms)
            logger.warning(f"Unknown provider '{provider}', using OpenAI-compatible adapter")

    def on_attach(self):
        # 초기 설정을 Config에 주입 (defaults layer에 없는 것만)
        for key, value in self._init_config.items():
            if self._config and self._config.get(key) is None:
                self._config.set(key, value, layer="defaults")

        # Config 변경 시 어댑터 재생성
        self._listen("config.changed", self._on_config_changed)

    def _on_config_changed(self, key: str = "", new: Any = None, **kwargs):
        if key == "provider" and new:
            # 프로바이더 자체가 바뀌면 어댑터 재생성
            self._create_adapter(
                new,
                self._cfg("base_url", ""),
                self._cfg("api_key", ""),
                self._cfg("timeout_ms", 30000),
                self._cfg("cwd", ""),
                self._cfg("auth", ""),
                self._cfg("write_dirs", None),
            )
            logger.info(f"Provider adapter switched to: {new}")
        elif key == "timeout_ms" and new is not None and self._adapter:
            self._adapter.set_timeout_ms(int(new))

    def set_timeout_ms(self, timeout_ms: int) -> None:
        """Update the adapter's subprocess / HTTP wait timeout. Delegates
        to the underlying adapter (§D.7 joint spec). Also updates the
        Config entry so the new value persists across adapter rebuilds.
        """
        if self._config is not None:
            self._config.set("timeout_ms", int(timeout_ms), layer="session")
        if self._adapter is not None:
            self._adapter.set_timeout_ms(int(timeout_ms))

    # --- Provides: llm_call ---

    def handle_llm_call(self, prompt: str = "", system: str = "", tools: list | None = None, messages: list[dict] | None = None) -> LLMResponse | LLMError:
        """
        LLM API 호출. 항상 Config에서 현재 model/temperature를 읽음.

        messages가 주어지면 멀티턴 대화 (prompt 무시).
        messages가 없으면 prompt를 단일 user 메시지로 처리.
        """
        if not self._adapter:
            return LLMError(error_type="connection", message="No adapter configured", retryable=False)

        # Config가 단일 소스 (호르몬 신호 수용)
        model = self._cfg("model", "llama3.1:8b")
        temperature = self._cfg("temperature", 0.7)
        max_tokens = self._cfg("max_tokens", 4096)
        effort = self._cfg("effort", "")
        # Habitat-cost knob, currently ollama-only: passed as a kwarg so an
        # adapter that does not take it is unaffected (see OllamaAdapter.call).
        num_ctx = self._cfg("num_ctx", None)

        with self._timed("llm_call"):
            start = time.perf_counter()
            try:
                result: AdapterResponse = self._adapter.call(
                    model=model,
                    prompt=prompt,
                    system=system,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    effort=effort,
                    **({"num_ctx": num_ctx} if num_ctx else {}),
                )

                elapsed = (time.perf_counter() - start) * 1000

                response = LLMResponse(
                    content=result.content,
                    model=model,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    latency_ms=elapsed,
                    tool_calls=result.tool_calls,
                    raw=result.raw,
                )

                # Bus에 응답 발행
                self._publish("llm.response", {
                    "model": model,
                    "tokens_in": response.tokens_in,
                    "tokens_out": response.tokens_out,
                    "latency_ms": response.latency_ms,
                })
                self._emit("llm.responded", model=model)

                # D-044 Layer 1 — brain provenance span (configured == actual,
                # since most adapters don't echo back a resolved model name)
                _tr.emit_brain_resolved(
                    self._organism,
                    configured=model,
                    actual=result.raw.get("model", model) if isinstance(result.raw, dict) else model,
                    reason="configured",
                )

                # D-081 Metabolic Gauge — per-call usage span. Adapters
                # catch their own errors and return an AdapterResponse
                # with content "[Error: ...]" rather than raising, so an
                # adapter-level failure arrives here, not in the except
                # branch below.
                raw = result.raw if isinstance(result.raw, dict) else {}
                stream_failed = bool(raw.get("timeout") or raw.get("stream_incomplete"))
                output_exhausted = raw.get("done_reason") == "length"
                generation_failed = stream_failed or output_exhausted
                is_err = generation_failed or (result.content or "").startswith("[Error:")
                if not is_err:
                    call_error_type = ""
                elif raw.get("timeout"):
                    call_error_type = "timeout"
                elif raw.get("error") == "quota_exhausted":
                    call_error_type = "quota"
                elif raw.get("fatigue_detail") or "reset_in_seconds" in raw:
                    # A rest is not an exhaustion. Collapsing both into "quota"
                    # made the span say the one thing the operator acts on —
                    # wait for a billing cycle, or change auth — when the truth
                    # was a rate-limit cooldown that clears in an hour. Wisp's
                    # M1 dyad was reported to JJ as quota-exhausted on this
                    # label while resilience already knew the cause was
                    # rate_limited with a 14:24 recovery time.
                    call_error_type = "fatigue"
                elif raw.get("stream_incomplete"):
                    call_error_type = "connection"
                elif output_exhausted:
                    call_error_type = "output_limit"
                elif raw.get("error") == "command_not_found":
                    call_error_type = "connection"
                else:
                    call_error_type = "model_error"
                provider_name = getattr(self._adapter, "provider_name", "")
                has_measured_usage = (
                    raw.get("prompt_eval_count") is not None
                    and raw.get("eval_count") is not None
                )
                _tr.emit_brain_call(
                    self._organism,
                    model=model,
                    provider=provider_name,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    # cursor_cli carries real in-band usage; the adapter declares
                    # measured-ness per call in raw (falls back to estimated).
                    token_source="measured" if (
                        (provider_name == "ollama" and has_measured_usage)
                        or raw.get("token_source") == "measured"
                    ) else "estimated",
                    latency_ms=elapsed,
                    outcome="error" if is_err else "ok",
                    error_type=call_error_type,
                    effort=effort,
                )

                if generation_failed:
                    return LLMError(
                        error_type=call_error_type,
                        message=(
                            f"Ollama generation {raw.get('done_reason') or 'failed'} "
                            f"after {raw.get('elapsed_ms', elapsed):.0f}ms"
                        ),
                        # Retrying the same prompt with the same output ceiling
                        # only buys the same truncation again.
                        retryable=not output_exhausted,
                        partial_content=result.content or "",
                        raw=raw,
                    )

                return response

            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                error_type = self._classify_error(e)
                self._emit("llm.failed", model=model, error=str(e))
                _tr.emit_brain_resolved(
                    self._organism,
                    configured=model,
                    actual=model,
                    reason="error",
                )
                # D-081 Metabolic Gauge — per-call usage span (call raised)
                _tr.emit_brain_call(
                    self._organism,
                    model=model,
                    provider=getattr(self._adapter, "provider_name", ""),
                    tokens_in=0,
                    tokens_out=0,
                    token_source="estimated",
                    latency_ms=elapsed,
                    outcome="error",
                    error_type=error_type,
                )
                return LLMError(
                    error_type=error_type,
                    message=str(e),
                    retryable=error_type != "auth",
                )

    # --- Provides: health_check ---

    def handle_health_check(self) -> dict:
        if not self._adapter:
            return {"status": "unhealthy", "error": "No adapter"}
        return self._adapter.health_check()

    # --- Provides: list_models ---

    def handle_list_models(self) -> list[str]:
        if not self._adapter:
            return []
        return self._adapter.list_models()

    # --- Error Classification ---

    @staticmethod
    def _classify_error(error: Exception) -> str:
        """에러를 분류 (OC shouldRetry 패턴)"""
        reason = getattr(error, "reason", None)
        if isinstance(error, (TimeoutError, socket.timeout)) or isinstance(
                reason, (TimeoutError, socket.timeout)):
            return "timeout"
        msg = str(error).lower()
        if "401" in msg or "403" in msg or "auth" in msg or "api_key" in msg:
            return "auth"
        if "429" in msg or "rate" in msg:
            return "rate_limit"
        if "timeout" in msg or "timed out" in msg:
            return "timeout"
        if "500" in msg or "502" in msg or "503" in msg:
            return "model_error"
        return "connection"
