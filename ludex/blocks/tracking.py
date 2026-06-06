"""
Tracking Block — 기록 장치 (실험 추적/비용/보고)

유기체의 모든 활동을 기록하는 장기.
턴 결과, 토큰 사용량, 비용, 에러를 JSONL로 저장.

Reference:
- CC: cost_tracker.py (label:units audit trail), transcript.py (append-only log)
- OC: infra/agent-events.ts (seq + ts structured events)
"""

from __future__ import annotations

import json
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)


@dataclass
class TrackEntry:
    """추적 기록 항목"""
    event: str
    data: dict
    timestamp: float = field(default_factory=time.time)
    turn: int = 0
    seq: int = 0


class TrackingBlock(Block):
    """
    실험 추적 블록. Bus 이벤트를 구독하여 자동 기록.

    provides: get_report, get_cost, get_entries
    requires: (없음) — 순수 관찰자, Bus/Signals만 구독

    JSONL 형식으로 파일 저장 (CC transcript 패턴).
    """

    name = "tracking"
    provides = [
        Port("get_report", description="Get experiment report"),
        Port("get_cost", description="Get total cost"),
        Port("get_entries", description="Get raw tracking entries"),
    ]
    requires = []

    def __init__(self, output_dir: str = "", experiment_name: str = ""):
        super().__init__()
        self._output_dir = output_dir
        self._experiment_name = experiment_name
        self._entries: list[TrackEntry] = []
        self._seq: int = 0

        # 집계
        self._total_tokens_in: int = 0
        self._total_tokens_out: int = 0
        self._total_turns: int = 0
        self._total_errors: int = 0
        self._total_latency_ms: float = 0.0
        self._models_used: dict[str, int] = {}
        self._start_time: float = time.time()

    def on_attach(self):
        """Bus/Signals 구독 — 순수 관찰자"""
        # Bus: 데이터 흐름 구독
        if self._bus:
            self._bus.subscribe("turn.result", self._on_turn_result, subscriber=self.name)
            self._bus.subscribe("llm.response", self._on_llm_response, subscriber=self.name)
            self._bus.subscribe("retry.attempted", self._on_retry, subscriber=self.name)
            self._bus.subscribe("benchmark", self._on_benchmark, subscriber=self.name)

        # Signals: 이벤트 구독
        self._listen("turn.started", self._on_turn_started)
        self._listen("turn.ended", self._on_turn_ended)
        self._listen("llm.failed", self._on_llm_failed)
        self._listen("circuit_breaker.opened", self._on_circuit_opened)
        self._listen("circuit_breaker.closed", self._on_circuit_closed)
        self._listen("homeostasis.regulation", self._on_regulation)
        self._listen("context.compacted", self._on_compacted)

    def on_detach(self):
        """분리 시 파일 저장"""
        if self._output_dir and self._entries:
            self._save_to_file()

    # --- Bus Handlers ---

    def _on_turn_result(self, msg):
        data = msg.data
        self._record("turn.result", data)
        self._total_turns += 1
        self._total_tokens_in += data.get("tokens_in", 0)
        self._total_tokens_out += data.get("tokens_out", 0)
        self._total_latency_ms += data.get("latency_ms", 0)
        model = data.get("model", "unknown")
        self._models_used[model] = self._models_used.get(model, 0) + 1

    def _on_llm_response(self, msg):
        self._record("llm.response", msg.data)

    def _on_retry(self, msg):
        self._record("retry.attempted", msg.data)

    def _on_benchmark(self, msg):
        self._record("benchmark", msg.data)

    # --- Signal Handlers ---

    def _on_turn_started(self, **kwargs):
        self._record("turn.started", kwargs)

    def _on_turn_ended(self, **kwargs):
        self._record("turn.ended", kwargs)
        if not kwargs.get("success", True):
            self._total_errors += 1

    def _on_llm_failed(self, **kwargs):
        self._record("llm.failed", kwargs)
        self._total_errors += 1

    def _on_circuit_opened(self, **kwargs):
        self._record("circuit_breaker.opened", kwargs)

    def _on_circuit_closed(self, **kwargs):
        self._record("circuit_breaker.closed", kwargs)

    def _on_regulation(self, **kwargs):
        reg = kwargs.get("regulation")
        if reg:
            self._record("homeostasis.regulation", {
                "type": reg.type, "trigger": reg.trigger,
                "action": reg.action, "reason": reg.reason,
            })

    def _on_compacted(self, **kwargs):
        self._record("context.compacted", kwargs)

    # --- Provides: get_report ---

    def handle_get_report(self) -> dict:
        """실험 보고서 생성"""
        elapsed = time.time() - self._start_time
        total_tokens = self._total_tokens_in + self._total_tokens_out
        avg_latency = self._total_latency_ms / max(self._total_turns, 1)
        tokens_per_turn = total_tokens / max(self._total_turns, 1)

        return {
            "experiment": self._experiment_name or self._cfg("experiment_name", "unnamed"),
            "duration_seconds": round(elapsed, 1),
            "total_turns": self._total_turns,
            "total_tokens": total_tokens,
            "tokens_in": self._total_tokens_in,
            "tokens_out": self._total_tokens_out,
            "tokens_per_turn": round(tokens_per_turn, 1),
            "total_errors": self._total_errors,
            "error_rate": round(self._total_errors / max(self._total_turns, 1), 3),
            "avg_latency_ms": round(avg_latency, 1),
            "models_used": dict(self._models_used),
            "total_entries": len(self._entries),
        }

    # --- Provides: get_cost ---

    def handle_get_cost(self) -> dict:
        """비용 요약 (Ollama=무료, API=토큰 기반 추정)"""
        # 간단한 비용 추정 (모델별 단가는 Config에서)
        cost_per_1k_in = self._cfg("cost_per_1k_input", 0.0)
        cost_per_1k_out = self._cfg("cost_per_1k_output", 0.0)

        cost_in = (self._total_tokens_in / 1000) * cost_per_1k_in
        cost_out = (self._total_tokens_out / 1000) * cost_per_1k_out

        return {
            "total_cost": round(cost_in + cost_out, 4),
            "cost_input": round(cost_in, 4),
            "cost_output": round(cost_out, 4),
            "tokens_in": self._total_tokens_in,
            "tokens_out": self._total_tokens_out,
            "currency": "USD",
        }

    # --- Provides: get_entries ---

    def handle_get_entries(self, event: str | None = None, limit: int = 100) -> list[dict]:
        """기록 항목 조회"""
        entries = self._entries
        if event:
            entries = [e for e in entries if e.event == event]
        return [
            {"event": e.event, "data": e.data, "timestamp": e.timestamp, "seq": e.seq}
            for e in entries[-limit:]
        ]

    # --- Internal ---

    def _record(self, event: str, data: dict):
        """이벤트 기록 (CC audit trail 패턴)"""
        self._seq += 1
        entry = TrackEntry(
            event=event,
            data=data,
            turn=data.get("turn_number", data.get("turn", 0)),
            seq=self._seq,
        )
        self._entries.append(entry)

    def _save_to_file(self):
        """JSONL 파일로 저장 (CC transcript 패턴)"""
        output_dir = Path(self._output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        name = self._experiment_name or "experiment"
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filepath = output_dir / f"{name}_{timestamp}.jsonl"

        with open(filepath, "w", encoding="utf-8") as f:
            # 헤더
            f.write(json.dumps({
                "type": "header",
                "experiment": name,
                "report": self.handle_get_report(),
                "config_snapshot": self._config.snapshot() if self._config else {},
            }, ensure_ascii=False) + "\n")

            # 기록 항목
            for entry in self._entries:
                f.write(json.dumps({
                    "type": "entry",
                    "event": entry.event,
                    "data": entry.data,
                    "timestamp": entry.timestamp,
                    "turn": entry.turn,
                    "seq": entry.seq,
                }, ensure_ascii=False) + "\n")

        logger.info(f"Tracking saved to {filepath}")
        return str(filepath)
