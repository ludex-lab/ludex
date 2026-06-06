"""
Resilience Block — 면역계 보조 (재시도, 폴백, 서킷 브레이커)

Provider를 감싸서 안정성을 제공하는 장기.
"Engine → Resilience.llm_call() → Provider.llm_call()" 래핑 체인.

Reference:
- OC: src/infra/backoff.ts computeBackoff(), src/infra/retry.ts retryAsync()
- OC: gateway/channel-health-monitor.ts (circuit breaker, restart policy)
- CC: query_engine.py (budget/turn limit as safety)
"""

from __future__ import annotations

import json
import re
import time
import random
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


_REMAINING_RE = re.compile(
    r"(?:recovers?|reset|resets?)\s+in\s+(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?",
    re.IGNORECASE,
)


def _parse_remaining_seconds(text: str) -> float:
    """Best-effort parse of "recovers in 11h42m" / "resets in 30m" etc."""
    if not text:
        return 0.0
    m = _REMAINING_RE.search(text)
    if not m:
        return 0.0
    h, mn, s = m.groups()
    return float((int(h) if h else 0) * 3600 +
                 (int(mn) if mn else 0) * 60 +
                 (int(s) if s else 0))

from ludex.core.block import Block
from ludex.core.port import Port
from ludex.blocks.provider import LLMResponse, LLMError

logger = logging.getLogger(__name__)


@dataclass
class RetryInfo:
    """재시도 정보 (모니터링/로깅용)"""
    attempt: int
    max_attempts: int
    delay_ms: float
    error_type: str
    error_message: str


class ResilienceBlock(Block):
    """
    안정성 블록. Provider를 감싸서 재시도, 백오프, 서킷 브레이커 적용.

    provides: llm_call (래핑된 버전)
    requires: llm_call (원본 Provider)

    래핑 체인: Engine → Resilience → Provider
    """

    name = "resilience"
    provides = [
        Port("llm_call", description="Resilient LLM call (wraps provider)"),
        Port("reset_circuit_breaker", description="Force-reset circuit breaker (immune override)"),
        Port("fatigue_state", description="Read current fatigue state (rested / fatigued / recovering)"),
    ]
    requires = [
        Port("llm_call", description="Raw LLM call from Provider", required=True),
    ]

    def __init__(
        self,
        max_retries: int = 3,
        initial_delay_ms: float = 300,
        max_delay_ms: float = 30000,
        backoff_factor: float = 2.0,
        jitter: float = 0.1,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_reset_ms: float = 60000,
        fallback_models: list[str] | None = None,
    ):
        super().__init__()
        # Retry config (OC retryAsync defaults)
        self.max_retries = max_retries
        self.initial_delay_ms = initial_delay_ms
        self.max_delay_ms = max_delay_ms
        self.backoff_factor = backoff_factor
        self.jitter = jitter

        # Circuit breaker (OC channel-health-monitor pattern)
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_reset_ms = circuit_breaker_reset_ms
        self._consecutive_failures: int = 0
        self._circuit_open: bool = False
        self._circuit_opened_at: float = 0.0

        # Fallback models
        self.fallback_models = fallback_models or []
        self._current_fallback_index: int = 0

        # Stats
        self._total_retries: int = 0
        self._total_failures: int = 0
        self._total_successes: int = 0

        # D-068 brain fatigue / self-care
        self._fatigue_until: float = 0.0   # epoch timestamp; 0 = not fatigued
        self._fatigue_cause: str = ""      # "quota_exhausted" / "rate_limited" / ...
        self._fatigue_started_at: float = 0.0  # for narrative reflection

    def on_attach(self):
        self._listen("config.changed", self._on_config_changed)
        # Hydrate fatigue state from sidecar if creature was fatigued in
        # a prior session and is still in cooldown window.
        self._load_fatigue_state()

    def _on_config_changed(self, key: str = "", **kwargs):
        if key == "fallback_models":
            self.fallback_models = kwargs.get("new", [])

    # --- Provides: llm_call (resilient wrapper) ---

    def handle_llm_call(self, prompt: str = "", system: str = "", tools: list | None = None, messages: list[dict] | None = None) -> LLMResponse | LLMError:
        """
        래핑된 LLM 호출. 내부적으로 Provider의 llm_call을 호출하되:
        1. 피로 (fatigue) 체크 — D-068: brain quota 고갈 시 자동 휴식
        2. 서킷 브레이커 체크
        3. 재시도 + 지수 백오프
        4. 에러 유형별 복구 전략
        5. 폴백 모델 전환
        """
        # D-068 fatigue check — brain is resting; skip the call entirely.
        now = time.time()
        if self._fatigue_until > now:
            remaining_s = self._fatigue_until - now
            return LLMError(
                error_type="fatigue",
                message=(
                    f"Creature is resting (cause: {self._fatigue_cause}, "
                    f"recovers in {self._fmt_duration(remaining_s)})"
                ),
                retryable=False,
            )
        elif self._fatigue_until > 0 and now >= self._fatigue_until:
            # Crossed the recovery threshold — emit recovery signal once.
            self._on_fatigue_recovered()

        # 서킷 브레이커 체크
        if self._circuit_open:
            if self._should_reset_circuit():
                self._close_circuit()
            else:
                return LLMError(
                    error_type="circuit_breaker",
                    message=f"Circuit breaker open ({self._consecutive_failures} consecutive failures)",
                    retryable=False,
                )

        # 재시도 루프
        last_error: Optional[LLMError] = None

        for attempt in range(1, self.max_retries + 1):
            self._emit("llm.calling", attempt=attempt, model=self._cfg("model"))

            result = self.call_port("llm_call", prompt=prompt, system=system, tools=tools, messages=messages)

            if isinstance(result, LLMResponse):
                # D-068: detect fatigue signal embedded in adapter raw.
                # Provider passes adapter.raw through unchanged; gemini_cli
                # surfaces {"error": "quota_exhausted"} when stderr matches
                # the quota pattern. Other adapters may set the same key
                # for rate limits / subscription caps.
                fatigue = self._extract_fatigue_signal(result)
                if fatigue is not None:
                    cause, reset_s, detail = fatigue
                    self.mark_fatigued(cause=cause, reset_in_seconds=reset_s, detail=detail)
                    return LLMError(
                        error_type="fatigue",
                        message=(
                            f"Creature fatigued (cause: {cause}, "
                            f"recovers in {self._fmt_duration(reset_s)})"
                        ),
                        retryable=False,
                    )
                # 성공
                self._on_success()
                return result

            # 실패
            last_error = result
            self._on_failure(result)

            # 재시도 불가능한 에러
            if not result.retryable:
                logger.warning(f"Non-retryable error: {result.error_type} — {result.message}")
                break

            # 마지막 시도였으면 break
            if attempt >= self.max_retries:
                break

            # 재시도 대기 (OC computeBackoff 패턴)
            delay = self._compute_backoff(attempt, result)
            retry_info = RetryInfo(
                attempt=attempt,
                max_attempts=self.max_retries,
                delay_ms=delay,
                error_type=result.error_type,
                error_message=result.message,
            )
            self._publish("retry.attempted", {
                "attempt": attempt,
                "delay_ms": delay,
                "error_type": result.error_type,
            })
            logger.info(f"Retry {attempt}/{self.max_retries} after {delay:.0f}ms — {result.error_type}")

            time.sleep(delay / 1000)

            # Rate limit이면 더 긴 대기
            if result.error_type == "rate_limit" and result.retry_after_ms:
                time.sleep(result.retry_after_ms / 1000)

        # 모든 재시도 실패 — 폴백 모델 시도
        if self.fallback_models and last_error:
            fallback_result = self._try_fallback(prompt, system, tools, messages)
            if fallback_result:
                return fallback_result

        return last_error or LLMError(
            error_type="exhausted",
            message="All retry attempts exhausted",
            retryable=False,
        )

    # --- Backoff (OC computeBackoff pattern) ---

    def _compute_backoff(self, attempt: int, error: LLMError) -> float:
        """
        지수 백오프 + jitter 계산.
        OC 공식: base * factor^(attempt-1), clamped to maxMs, + jitter
        """
        # Rate limit이면 retry_after 우선
        if error.error_type == "rate_limit" and error.retry_after_ms:
            return max(error.retry_after_ms, self.initial_delay_ms)

        base = self.initial_delay_ms * (self.backoff_factor ** (attempt - 1))
        jitter_amount = base * self.jitter * random.random()
        delay = min(base + jitter_amount, self.max_delay_ms)
        return max(delay, self.initial_delay_ms)

    # --- Circuit Breaker ---

    def _on_success(self):
        self._consecutive_failures = 0
        self._total_successes += 1
        if self._circuit_open:
            self._close_circuit()
        # Vital signs 업데이트
        if self._config:
            self._config.set("_consecutive_failures", 0, layer="session")
            self._config.set("_circuit_breaker_open", False, layer="session")

    def _on_failure(self, error: LLMError):
        self._consecutive_failures += 1
        self._total_failures += 1
        if self._consecutive_failures >= self.circuit_breaker_threshold:
            self._open_circuit()
        # Vital signs 업데이트
        if self._config:
            self._config.set("_consecutive_failures", self._consecutive_failures, layer="session")

    def _open_circuit(self):
        if not self._circuit_open:
            self._circuit_open = True
            self._circuit_opened_at = time.time()
            self._emit("circuit_breaker.opened", failures=self._consecutive_failures)
            logger.warning(f"Circuit breaker OPENED after {self._consecutive_failures} failures")
            if self._config:
                self._config.set("_circuit_breaker_open", True, layer="session")

    def _close_circuit(self):
        if self._circuit_open:
            self._circuit_open = False
            self._consecutive_failures = 0
            self._emit("circuit_breaker.closed")
            logger.info("Circuit breaker CLOSED — system recovered")
            if self._config:
                self._config.set("_circuit_breaker_open", False, layer="session")

    def _should_reset_circuit(self) -> bool:
        elapsed = (time.time() - self._circuit_opened_at) * 1000
        return elapsed >= self.circuit_breaker_reset_ms

    # --- Fallback Models ---

    def _try_fallback(self, prompt: str, system: str, tools: list | None, messages: list[dict] | None = None) -> Optional[LLMResponse]:
        """폴백 모델로 전환 시도"""
        for fallback_model in self.fallback_models:
            if self._config:
                old_model = self._config.get("model")
                self._config.set("model", fallback_model)
                logger.info(f"Trying fallback model: {fallback_model}")

                result = self.call_port("llm_call", prompt=prompt, system=system, tools=tools, messages=messages)

                if isinstance(result, LLMResponse):
                    self._emit("model.fallback_succeeded", old_model=old_model, new_model=fallback_model)
                    return result

                # 실패하면 원래 모델로 복원
                self._config.set("model", old_model)

        return None

    # --- Immune Integration ---

    def handle_reset_circuit_breaker(self) -> dict:
        """면역계에서 서킷 브레이커를 강제 리셋. 모델 교체 후 재시도 허용."""
        was_open = self._circuit_open
        self._close_circuit()
        self._consecutive_failures = 0
        logger.info("Circuit breaker force-reset by immune system")
        return {"was_open": was_open, "now_open": False}

    # --- D-068 Brain Fatigue / Self-Care ---

    @staticmethod
    def _extract_fatigue_signal(response: "LLMResponse") -> Optional[tuple[str, float, str]]:
        """Inspect an LLMResponse for substrate-level fatigue markers.

        Returns (cause, reset_in_seconds, detail) when the response indicates
        the brain has hit a quota / rate / subscription wall, else None.

        Source of truth is `response.raw`, which carries the adapter's raw
        dict unchanged through ProviderBlock.
        """
        raw = getattr(response, "raw", None)
        if not isinstance(raw, dict):
            return None
        err = raw.get("error", "")
        # Adapter-marked quota / rate-limit signals.
        if err in ("quota_exhausted", "rate_limited", "subscription_limit"):
            content = getattr(response, "content", "") or ""
            # Adapter-supplied reset window wins (codex_cli parses the
            # weekly absolute timestamp; D-068 default of 1h was burning
            # 22 redundant retries on Anvil before this path existed).
            adapter_reset = raw.get("reset_in_seconds")
            if isinstance(adapter_reset, (int, float)) and adapter_reset > 0:
                reset_s = float(adapter_reset)
            else:
                # Try to parse a remaining duration from the content message.
                reset_s = _parse_remaining_seconds(content)
                if reset_s <= 0:
                    # Default cooldown: 1 hour. Caller can override later.
                    reset_s = 3600.0
            return err, reset_s, content[:200]
        # Fall-through: some adapters embed quota text in stderr without
        # a structured marker yet. Future-compat hook — leave None.
        return None

    def handle_fatigue_state(self) -> dict:
        """Read current fatigue state.

        Returns dict with:
          - state: "rested" | "fatigued"
          - cause: empty when rested
          - remaining_s: seconds until recovery (0 when rested)
          - started_at: epoch when fatigue began (0 when rested)
        """
        now = time.time()
        if self._fatigue_until > now:
            return {
                "state": "fatigued",
                "cause": self._fatigue_cause,
                "remaining_s": self._fatigue_until - now,
                "remaining_human": self._fmt_duration(self._fatigue_until - now),
                "started_at": self._fatigue_started_at,
                "recovers_at": self._fatigue_until,
            }
        return {
            "state": "rested",
            "cause": "",
            "remaining_s": 0.0,
            "remaining_human": "",
            "started_at": 0.0,
            "recovers_at": 0.0,
        }

    def mark_fatigued(self, cause: str, reset_in_seconds: float, detail: str = ""):
        """Enter fatigue state. Public so adapters / heartbeat can manually mark.

        Persisted to habitat sidecar so the creature stays fatigued across
        process restarts within the cooldown window.
        """
        now = time.time()
        # If already fatigued, extend window only if new reset is later.
        new_until = now + max(reset_in_seconds, 0)
        if new_until <= self._fatigue_until:
            return
        was_rested = self._fatigue_until <= now
        self._fatigue_until = new_until
        self._fatigue_cause = cause or "unknown"
        if was_rested:
            self._fatigue_started_at = now
            self._on_fatigue_started(detail=detail)
        self._save_fatigue_state()

    def _on_fatigue_started(self, detail: str = ""):
        self._emit(
            "creature.fatigued",
            cause=self._fatigue_cause,
            recovers_at=self._fatigue_until,
            detail=detail,
        )
        logger.warning(
            f"Creature fatigued (cause={self._fatigue_cause}, "
            f"recovers in {self._fmt_duration(self._fatigue_until - time.time())})"
        )
        # D-068 Phase 2: narrative reflection trigger. Append a short
        # Self-care line to SELF.md so the creature's narrative identity
        # carries this experience into future reflections. Cheap and
        # self-contained — no brain call (the brain just got exhausted).
        self._append_self_care_note(detail=detail)

    def _append_self_care_note(self, detail: str = ""):
        cfg = self._config
        if cfg is None or not hasattr(cfg, "get"):
            return
        habitat_dir = cfg.get("habitat_dir", "")
        if not habitat_dir:
            return
        self_md = Path(habitat_dir) / "SELF.md"
        try:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(self._fatigue_started_at))
            recovers = time.strftime("%Y-%m-%d %H:%M", time.localtime(self._fatigue_until))
            note_lines = [
                "",
                "## Self-care",
                "",
                f"- {ts} — Brain reached its capacity for the day "
                f"(cause: `{self._fatigue_cause}`). Resting until "
                f"{recovers}. " +
                ("" if not detail else f"Detail: {detail[:200]}"),
                "",
            ]
            existing = self_md.read_text(encoding="utf-8") if self_md.exists() else ""
            # If the file already has a "## Self-care" section, append to it
            # in-place; otherwise add a new section.
            if "## Self-care" in existing:
                # Insert new bullet after the section header.
                lines = existing.splitlines()
                for i, line in enumerate(lines):
                    if line.strip() == "## Self-care":
                        # Find where this section's bullets end and insert
                        insert_at = i + 1
                        # Skip blank line right after header if present
                        while insert_at < len(lines) and not lines[insert_at].strip():
                            insert_at += 1
                        # Insert new bullet at top of section's bullets
                        bullet = note_lines[3]
                        lines.insert(insert_at, bullet)
                        break
                self_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
            else:
                self_md.write_text(
                    existing.rstrip() + "\n" + "\n".join(note_lines),
                    encoding="utf-8",
                )
            logger.info(f"Self-care note appended to {self_md}")
        except Exception as e:
            logger.warning(f"Failed to append Self-care note: {e}")

    def _on_fatigue_recovered(self):
        was_cause = self._fatigue_cause
        self._fatigue_until = 0.0
        self._fatigue_cause = ""
        self._fatigue_started_at = 0.0
        self._save_fatigue_state()
        self._emit("creature.recovered", prior_cause=was_cause)
        logger.info(f"Creature recovered from fatigue (was: {was_cause})")

    def _fatigue_sidecar_path(self) -> Optional[Path]:
        """Where to persist fatigue state. Sidecar `.fatigue.json` in
        creature's habitat dir, separate from yaml so we don't trigger
        a full config round-trip on every fatigue update."""
        cfg = self._config
        if cfg is None or not hasattr(cfg, "get"):
            return None
        habitat_dir = cfg.get("habitat_dir", "")
        if not habitat_dir:
            return None
        return Path(habitat_dir) / ".fatigue.json"

    def _load_fatigue_state(self):
        path = self._fatigue_sidecar_path()
        if path is None or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._fatigue_until = float(data.get("fatigue_until", 0.0))
            self._fatigue_cause = data.get("cause", "")
            self._fatigue_started_at = float(data.get("started_at", 0.0))
            now = time.time()
            if self._fatigue_until <= now and self._fatigue_until > 0:
                # Stale — already recovered between sessions. Clear.
                self._on_fatigue_recovered()
        except Exception as e:
            logger.warning(f"Failed to load fatigue sidecar {path}: {e}")

    def _save_fatigue_state(self):
        path = self._fatigue_sidecar_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "fatigue_until": self._fatigue_until,
                        "cause": self._fatigue_cause,
                        "started_at": self._fatigue_started_at,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Failed to save fatigue sidecar {path}: {e}")

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        seconds = max(int(seconds), 0)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h{m:02d}m"
        if m:
            return f"{m}m{s:02d}s"
        return f"{s}s"

    # --- Stats ---

    def get_stats(self) -> dict:
        return {
            "total_retries": self._total_retries,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "consecutive_failures": self._consecutive_failures,
            "circuit_breaker_open": self._circuit_open,
            "fatigue": self.handle_fatigue_state(),
        }
