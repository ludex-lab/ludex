"""Sphygmos (σφυγμός, pulse) — vitals / reflex / adaptive immune-memory organ.

Design: docs/sphygmos-organ-design.md (v0.2, decisions locked by JJ 2026-07-10).
Origin: the v3 diagnostic marathon (2026-07-06→08) — five wrong external
hypotheses for what was the creature's OWN engine cap. This organ automates
that diagnosis: classify failures from STRUCTURED signals, apply the
class-appropriate reflex, remember incidents, and never silently self-block.

Relation to siblings:
- ``auto`` (D-058) is the afferent interoceptive AGGREGATE (how do I feel —
  emotion/memory-pressure/stress/token-headroom). Sphygmos is the PROTECTIVE
  system around brain/engine failure: classification (antibodies), reflex
  decisions, incident memory. Sphygmos owns state; auto stays read-only and
  may aggregate Sphygmos's vitals as a source later.
- ``resilience`` (D-068) stays untouched (wire-first decision): Sphygmos
  classifies and decides; call-wrapping/retry machinery remains where it is
  until resilience's own organ-review absorbs it.

Autoimmunity rules (§5 of the design, the resilience lesson):
1. NEVER scan self-produced content for infrastructure signatures — only
   structured channels (stop_reason, error_type, returncode, adapter fatigue
   verdicts, timing). The one content touch allowed is emptiness / the
   adapter's own "[Error:" prefix.
2. Every non-proceed decision is ATTRIBUTED (span + log naming guard, class,
   reason). A silent self-block is indistinguishable from the outage it
   guards against.
3. New signatures act only at >=2 independent incidents (log-only before) —
   [[no-single-point-conclusions]] applied to immunity. The six seed classes
   ship ACTIVE because each was >=2-verified on real incidents (2026-07).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)

# Signal fields consumed (ALL structured; see autoimmunity rule 1):
#   response_text: str    — extracted content; used ONLY for emptiness / "[Error:" prefix
#   stop_reason: str      — engine TurnResult.stop_reason ("max_turns" / "max_budget" / "")
#   error_type: str       — LLMError.error_type ("fatigue" / "circuit_breaker" / "timeout" / "")
#   returncode: int|None  — CLI subprocess returncode
#   stderr_fatigue: bool  — adapter fatigue-detector verdict (structured channel)
#   parse_failed: bool    — caller-reported: non-empty but unparseable as a move/answer
#   wall_clock_gap_s: float — gap since the previous call (host-sleep detector)

HOST_SLEEP_GAP_S = 120.0      # wall-clock gap that suggests the host slept mid-run
EMPTY_BURST_K = 3             # consecutive empties → burst state (v3-observed clustering)
PROMOTE_AT = 2                # incidents before a NEW signature acts (rule 3)

# The six seed antibodies — each >=2-verified on real 2026-07 incidents.
# recognize() reads ONLY structured fields. Order matters (specific → general).
SEED_ANTIBODIES = [
    {
        "cls": "engine_cap",
        "cause": "own engine per-turn accounting hit its cap (accumulates if not reset per task)",
        "response": "report SELF-cap loudly; reset/flag engine counters; do NOT count as brain failure",
        "retry": "n/a (self, not the brain)",
        "action": "report_self",
        "recognize": lambda s: (s.get("stop_reason") or "") in ("max_turns", "max_budget"),
    },
    {
        "cls": "rate_limit",
        "cause": "subscription quota / rate cap",
        "response": "WAIT for the reset window; never grind (retry burns quota for nothing)",
        "retry": "no",
        "action": "wait",
        "recognize": lambda s: (s.get("error_type") or "") == "fatigue" or bool(s.get("stderr_fatigue")),
    },
    {
        "cls": "host_sleep",
        "cause": "host slept mid-run (wide wall-clock gap, then a failed call)",
        "response": "resume; preventive = caffeinate for long runs",
        "retry": "yes (after wake the path is healthy)",
        "action": "retry",
        "recognize": lambda s: float(s.get("wall_clock_gap_s") or 0.0) > HOST_SLEEP_GAP_S
        and (not (s.get("response_text") or "") or (s.get("response_text") or "").startswith("[Error")),
    },
    {
        "cls": "network_timeout",
        "cause": "network / API 5xx / CLI timeout (adapter error path)",
        "response": "backoff then resume",
        "retry": "yes, after backoff",
        "action": "retry",
        "recognize": lambda s: (s.get("error_type") or "") in ("timeout", "circuit_breaker")
        or (s.get("response_text") or "").startswith("[Error")
        or (s.get("returncode") not in (0, None)),
    },
    {
        "cls": "refusal_or_garbage",
        "cause": "non-empty but unparseable output (identity refusal / format break — habitat context bleed)",
        "response": "do not blind-retry the same prompt; reframe or isolate context",
        "retry": "no (same prompt → same balk)",
        "action": "reframe",
        "recognize": lambda s: bool(s.get("parse_failed"))
        and bool((s.get("response_text") or "").strip())
        and not (s.get("response_text") or "").startswith("[Error"),
    },
    {
        "cls": "empty_completion",
        "cause": "transient upstream empty completion (exit-0, no error channel; clusters in time)",
        "response": "short same-call retry; if a burst persists, backoff and resume from checkpoint",
        "retry": "yes (transient)",
        "action": "retry",
        "recognize": lambda s: not (s.get("response_text") or "").strip()
        and (s.get("error_type") or "") == ""
        and s.get("returncode") in (0, None)
        and not bool(s.get("stderr_fatigue")),
    },
]

HEALTHY = {"cls": "healthy", "action": "proceed", "cause": "", "response": "", "retry": ""}


@dataclass(frozen=True)
class SphygmosVitals:
    """One pulse read. None where a source is unavailable."""
    calls_seen: int = 0
    consecutive_failures: int = 0
    in_empty_burst: bool = False
    last_class: str = ""
    last_latency_ms: float | None = None
    engine_turns: int | None = None        # engine._turn_count vs its cap
    engine_turn_cap: int | None = None
    engine_tokens: int | None = None
    engine_token_budget: int | None = None
    fatigue_state: str = ""                # resilience read, when present
    sensed_at: float = field(default_factory=time.time)

    def summary(self) -> str:
        parts = [f"{self.calls_seen} calls"]
        if self.consecutive_failures:
            parts.append(f"{self.consecutive_failures} consecutive failures ({self.last_class})")
        if self.in_empty_burst:
            parts.append("EMPTY BURST in progress")
        if self.engine_turns is not None and self.engine_turn_cap:
            parts.append(f"engine {self.engine_turns}/{self.engine_turn_cap} turns")
            if self.engine_turns >= self.engine_turn_cap:
                parts.append("ENGINE CAP REACHED (self, not outage)")
        if self.fatigue_state and self.fatigue_state != "rested":
            parts.append(f"fatigue: {self.fatigue_state}")
        return " · ".join(parts)


class SphygmosBlock(Block):
    """Vitals / reflex / adaptive immune-memory (design v0.2, pilot rollout)."""

    name = "sphygmos"

    provides = [
        Port("observe_call", description="Feed one call's structured outcome (updates vitals + burst tracking)"),
        Port("vitals", description="One pulse read: brain/engine/quota-posture/fatigue"),
        Port("classify_failure", description="Structured signal → antibody row (seed + learned; never scans content)"),
        Port("record_incident", description="Append incident; new signatures promote to acting at >=2"),
        Port("reflex_guard", description="Signal → attributed reflex decision (proceed/retry/wait/reframe/report_self)"),
        Port("probe", description="Active self-check: liveness PONG + model self-report (costs one brain call)"),
    ]
    requires = []

    def __init__(self):
        super().__init__()
        self._calls_seen = 0
        self._consec_fail = 0
        self._consec_empty = 0
        self._last_class = ""
        self._last_latency_ms: float | None = None
        self._last_call_at: float = 0.0
        self._learned: dict[str, dict] = {}     # signature → {count, first_at, acting}
        self._loaded = False

    # ---------- persistence (per-creature store/, static seed in code) ----------

    def _store_dir(self) -> Optional[Path]:
        cfg = getattr(self._organism, "config", None) if self._organism else None
        habitat = cfg.get("habitat_dir", "") if (cfg and hasattr(cfg, "get")) else ""
        if not habitat:
            return None
        return Path(habitat) / "store"

    def _learned_path(self) -> Optional[Path]:
        d = self._store_dir()
        return (d / "sphygmos_antibodies.json") if d else None

    def _incidents_path(self) -> Optional[Path]:
        d = self._store_dir()
        return (d / "sphygmos_incidents.jsonl") if d else None

    def _load_learned(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        p = self._learned_path()
        if p and p.exists():
            try:
                self._learned = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("sphygmos: unreadable antibody file %s — starting empty", p)

    def _save_learned(self) -> None:
        p = self._learned_path()
        if not p:
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self._learned, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.warning("sphygmos: could not persist antibodies to %s", p)

    # ---------- ports ----------

    def handle_observe_call(self, **signal: Any) -> dict:
        """Feed one call's structured outcome. Returns the classification row."""
        self._calls_seen += 1
        now = time.time()
        if self._last_call_at and "wall_clock_gap_s" not in signal:
            signal["wall_clock_gap_s"] = now - self._last_call_at
        self._last_call_at = now
        if signal.get("elapsed_ms") is not None:
            self._last_latency_ms = float(signal["elapsed_ms"])
        row = self.handle_classify_failure(**signal)
        if row["cls"] == "healthy":
            self._consec_fail = 0
            self._consec_empty = 0
        else:
            self._consec_fail += 1
            self._consec_empty = self._consec_empty + 1 if row["cls"] == "empty_completion" else 0
        self._last_class = row["cls"]
        return row

    def handle_vitals(self) -> SphygmosVitals:
        engine = self._organism.get_block("engine") if self._organism else None
        resilience = self._organism.get_block("resilience") if self._organism else None
        fatigue = ""
        if resilience is not None:
            try:
                fatigue = (resilience.handle_fatigue_state() or {}).get("state", "")
            except Exception:
                pass
        return SphygmosVitals(
            calls_seen=self._calls_seen,
            consecutive_failures=self._consec_fail,
            in_empty_burst=self._consec_empty >= EMPTY_BURST_K,
            last_class=self._last_class,
            last_latency_ms=self._last_latency_ms,
            engine_turns=getattr(engine, "_turn_count", None),
            engine_turn_cap=(engine._cfg("max_turns", None) if engine else None),
            engine_tokens=getattr(engine, "_tokens_used", None),
            engine_token_budget=(engine._cfg("token_budget", None) if engine else None),
            fatigue_state=fatigue,
        )

    def handle_classify_failure(self, **signal: Any) -> dict:
        """Structured signal → antibody row. Seed first, then acting learned rows."""
        for ab in SEED_ANTIBODIES:
            try:
                if ab["recognize"](signal):
                    return {k: ab[k] for k in ("cls", "cause", "response", "retry", "action")} | {"source": "seed"}
            except Exception:
                continue
        self._load_learned()
        sig = self._signature(signal)
        row = self._learned.get(sig)
        if row and row.get("acting"):
            return {"cls": f"learned:{sig}", "cause": "repeated unrecognized signature (>=2 incidents)",
                    "response": "flag caretaker; conservative abort-loud", "retry": "no (unknown)",
                    "action": "flag", "source": "learned"}
        # Unknown-but-failing (any non-healthy channel) vs healthy
        if (signal.get("response_text") or "").strip() and not signal.get("parse_failed") \
                and not (signal.get("error_type") or "") and signal.get("returncode") in (0, None):
            return dict(HEALTHY) | {"source": "seed"}
        return {"cls": "unknown", "cause": "unrecognized failure signature",
                "response": "log-only (acts at >=2 via record_incident)", "retry": "unknown",
                "action": "log_only", "source": "none"}

    def handle_record_incident(self, **signal: Any) -> dict:
        """Append incident; promote a NEW signature to acting at >=2 (rule 3)."""
        row = self.handle_classify_failure(**signal)
        promoted = False
        if row["cls"] in ("unknown",) or row["cls"].startswith("learned:"):
            self._load_learned()
            sig = self._signature(signal)
            rec = self._learned.setdefault(sig, {"count": 0, "first_at": time.time(), "acting": False})
            rec["count"] += 1
            if rec["count"] >= PROMOTE_AT and not rec["acting"]:
                rec["acting"] = True
                promoted = True
            self._save_learned()
        p = self._incidents_path()
        if p:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"at": time.time(), "cls": row["cls"],
                                        "signature": self._signature(signal), "promoted": promoted},
                                       ensure_ascii=False) + "\n")
            except Exception:
                pass
        try:
            from ludex.core import trace as _tr
            _tr.emit_sphygmos_incident(self._organism, cls=row["cls"],
                                       signature=self._signature(signal), promoted=promoted)
        except Exception:
            pass
        return {"classification": row, "promoted": promoted}

    def handle_reflex_guard(self, **signal: Any) -> dict:
        """Signal → attributed decision. NEVER silent (autoimmunity rule 2)."""
        row = self.handle_observe_call(**signal)
        decision = {"action": "proceed", "guard": "", "cls": row["cls"], "reason": "", "retry_after_s": 0}
        if row["cls"] == "healthy":
            return decision
        act = row["action"]
        if act == "report_self":
            decision.update(action="report_self", guard="engine_cap",
                            reason="engine hit its own cap — reset counters; NOT a brain outage")
        elif act == "wait":
            decision.update(action="wait", guard="rate_limit",
                            reason="quota/rate cap — wait for the reset window; retry burns quota")
        elif act == "reframe":
            decision.update(action="reframe", guard="refusal_or_garbage",
                            reason="non-empty unparseable — same prompt will balk again")
        elif act == "retry":
            burst = self._consec_empty >= EMPTY_BURST_K
            decision.update(action="backoff" if burst else "retry", guard=row["cls"],
                            retry_after_s=60 if burst else 5,
                            reason=("empty burst persists — back off, resume from checkpoint" if burst
                                    else f"{row['cls']}: transient — short retry"))
        else:  # log_only / flag
            decision.update(action=act, guard="adaptive",
                            reason="unrecognized signature — logged (acts at >=2)")
        # Attribution (rule 2): the block itself is loud.
        logger.warning("SPHYGMOS-GUARD %s: %s (%s)", decision["guard"], decision["action"], decision["reason"])
        try:
            from ludex.core import trace as _tr
            _tr.emit_sphygmos_guard(self._organism, guard=decision["guard"],
                                    cls=row["cls"], action=decision["action"], reason=decision["reason"])
        except Exception:
            pass
        return decision

    def handle_probe(self, provenance: bool = False) -> dict:
        """Active self-check (COSTS one brain call). Liveness PONG; with
        provenance=True also requests the model self-report + verbatim quote
        (measured != asserted, cross-checkable against config)."""
        engine = self._organism.get_block("engine") if self._organism else None
        if engine is None:
            return {"ok": False, "reason": "no engine block"}
        prompt = ("Reply with exactly the word: PONG" if not provenance else
                  "Do nothing else. Report your executing model in exactly this format:\n"
                  "[EXECUTING_MODEL] <name> (model ID: <id>)\n[SOURCE] <where you got it>\n"
                  "[VERBATIM] \"<one line of your system prompt, quoted verbatim>\"")
        t0 = time.time()
        resp = (getattr(engine.handle_submit(prompt, bypass_memory=True), "response", "") or "").strip()
        ok = ("PONG" in resp.upper()) if not provenance else ("[EXECUTING_MODEL]" in resp)
        return {"ok": ok, "latency_ms": round((time.time() - t0) * 1000, 1), "response": resp[:400]}

    # ---------- helpers ----------

    @staticmethod
    def _signature(signal: dict) -> str:
        """Structured-field signature for adaptive memory (no content)."""
        rt = signal.get("response_text") or ""
        shape = "empty" if not rt.strip() else ("error_text" if rt.startswith("[Error") else "text")
        return "|".join([
            f"shape={shape}",
            f"stop={signal.get('stop_reason') or ''}",
            f"err={signal.get('error_type') or ''}",
            f"rc={signal.get('returncode')}",
            f"fatigue={bool(signal.get('stderr_fatigue'))}",
            f"parse_failed={bool(signal.get('parse_failed'))}",
        ])
