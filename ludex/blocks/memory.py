"""
Memory Block — 기억 관리 (Soft Shell 첫 걸음)

세션 간 기억을 저장하고, 검색하고, 정리하는 장기.
파일 기반 (JSONL), 외부 의존성 없음.

기억 유형:
- episodic: 경험 기반 ("어제 Trust Game에서 llama가 배신했다")
- semantic: 사실/지식 ("exaone은 기본 협력 성향")
- prospective: 할 일 ("Luca에게 파일럿 코드 보내기")

Reference:
- OC: extensions/memory-core/ (SQLite + FTS + vector), flush-plan.ts
- OC: extensions/memory-lancedb/ (vector search, auto-capture/recall)
- CC: memdir/ (archived), MEMORY.md (file-based index)
- Anthropic AutoDream: date normalization, contradiction removal, pruning
"""

from __future__ import annotations

import json
import math
import time
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)


# ============================================================
# Memory Data Types
# ============================================================

@dataclass
class Memory:
    """하나의 기억.

    memory_type: D-024 Sprint 1 expanded vocabulary — see
    ludex/core/memory_types.py for the canonical 8 types (identity,
    belief, preference, relationship, skill, episodic, routine,
    transient) plus legacy aliases (semantic, prospective). Decay
    and tier bucketing derive from memory_type.

    token_count: estimate_tokens(content) at save time. Used by
    consolidation to enforce per-brain capacity budgets. 0 means
    "not yet computed" (old JSONL without this field); recomputed
    lazily when needed.

    status: "active" | "archived" | "deleted" |
    "candidate_for_distillation" (Phase 2 new — warm-tier memory
    that the dream cycle has selected for narrative distillation,
    awaiting the creature's reflect() to integrate).
    """
    id: str
    content: str
    memory_type: str
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5     # 0.0 ~ 1.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    source: str = ""
    status: str = "active"
    token_count: int = 0
    metadata: dict = field(default_factory=dict)
    protected: bool = False  # D-071 — identity-type writes default True; forgetting pass skips unconditionally
    # D-071 pillar 3 — retrieval-failure model. forgotten=True means
    # "trace fades but isn't erased": the memory stays on disk, but
    # `handle_recall` filters it out by default. Set via
    # `handle_mark_forgotten`; pillar 4 forgetting pass populates these.
    forgotten: bool = False
    forgotten_at: float = 0.0
    forgotten_reason: str = ""
    # D-071 pillar 4 — recall_count feeds the forget-score divisor
    # (often-recalled = important, less likely to forget). Bumped in
    # `handle_recall` when the memory surfaces in the result set; also
    # nudges importance per design (recall_count boost: +0.05 capped
    # at 0.95). The future forgetting pass uses recall_count via
    # `compute_forget_score`.
    recall_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type,
            "tags": self.tags,
            "importance": self.importance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source,
            "status": self.status,
            "token_count": self.token_count,
            "metadata": self.metadata,
            "protected": self.protected,
            "forgotten": self.forgotten,
            "forgotten_at": self.forgotten_at,
            "forgotten_reason": self.forgotten_reason,
            "recall_count": self.recall_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Memory:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @property
    def is_forgettable(self) -> bool:
        """D-071 pillar 2 — single rule the forgetting pass (pillar 4)
        consults. Composes all no-forget conditions so future code can
        just check `if not m.is_forgettable: continue`. Independent of
        `handle_recall`'s deprecated-tag filter (recall scope ≠
        forget eligibility — a memory can be recall-suppressed via
        `deprecated:` tag while still being protected from forgetting
        for archaeological reasons)."""
        if self.protected:
            return False
        if self.status != "active":
            # archived / deleted are past the forget gate already
            return False
        return True


@dataclass(frozen=True)
class RecallResult:
    """기억 검색 결과"""
    memory: Memory
    relevance: float    # 0.0 ~ 1.0 검색 관련도


@dataclass
class ConsolidationReport:
    """기억 정리 보고"""
    promoted: int       # episodic → semantic으로 승격된 수
    archived: int       # 보관 처리된 수
    deleted: int        # 삭제된 수
    contradictions: int # 모순 해결된 수
    total_remaining: int


@dataclass
class HealthReport:
    """D-024 Sprint 2 — memory health assertion summary.

    Five assertions, each returns PASS / FAIL. Returns overall
    pass rate and letter grade (A/B/C/D). Ludex-specific assertions,
    not a direct port of memkraft's 5 — adapted for creature memory
    where there's no 'inbox' or 'entity-link graph' to check.
    """
    source_attribution: bool     # ≥ 80% of active memories have a non-empty source
    budget_compliance: bool      # HOT and WARM tokens under configured budget
    identity_grounded: bool      # at least one active identity memory
    no_duplicates: bool          # no near-duplicate memory pairs
    consolidation_fresh: bool    # last consolidate < 7 days ago
    source_coverage: float       # 0.0–1.0, actual source attribution rate
    hot_tokens: int
    hot_budget: int
    warm_tokens: int
    warm_budget: int
    duplicate_count: int
    days_since_consolidation: float
    notes: list[str]

    @property
    def pass_count(self) -> int:
        return sum([
            self.source_attribution, self.budget_compliance,
            self.identity_grounded, self.no_duplicates,
            self.consolidation_fresh,
        ])

    @property
    def pass_rate(self) -> float:
        return self.pass_count / 5.0

    @property
    def grade(self) -> str:
        pc = self.pass_count
        if pc == 5: return "A"
        if pc == 4: return "B"
        if pc == 3: return "C"
        return "D"


# ============================================================
# Memory Block
# ============================================================

class MemoryBlock(Block):
    """
    기억 관리 블록.

    provides: remember, recall, forget, consolidate, list_memories
    requires: (없음)

    파일 기반 저장 (JSONL). 외부 DB 의존성 없음.
    """

    name = "memory"
    provides = [
        Port("remember", description="Store a memory"),
        Port("recall", description="Search memories by query"),
        Port("forget", description="Delete or archive a memory"),
        Port("consolidate", description="Consolidate/clean up memories (dream)"),
        Port("list_memories", description="List memories by type/tag"),
        Port("health_check", description="D-024 Sprint 2 — memory health assertions"),
    ]
    requires = []

    def __init__(self, storage_dir: str = "", auto_capture: bool = True,
                 weight_check_interval: int = 10):
        super().__init__()
        self._storage_dir = storage_dir
        self._auto_capture = auto_capture
        self._memories: dict[str, Memory] = {}
        self._next_id: int = 1
        self._loaded: bool = False
        self._weight_check_interval = weight_check_interval
        self._turns_since_weight_check: int = 0
        self._last_consolidated_at: float = 0.0  # D-024 Sprint 2 hygiene
        # Most recent (query, results) from handle_recall. Exposed via
        # `last_recall` so callers like the LxM adapter (joint spec §A.3)
        # can read the same top-N that EngineBlock.handle_submit already
        # computed, avoiding a double TF-IDF pass per turn.
        self._last_recall: tuple[str, list[RecallResult]] | None = None

    def on_attach(self):
        # 스토리지 디렉토리 설정
        if not self._storage_dir:
            self._storage_dir = self._cfg("memory_dir", "./ludex_memory")

        # 기존 기억 로드
        self._load()

        # 자동 캡처: 턴 결과를 episodic 기억으로 저장
        if self._auto_capture:
            self._listen("turn.ended", self._auto_capture_turn)

        # Periodic weight check on turn end
        self._listen("turn.ended", self._periodic_weight_check)

        # Check habitat weight on load — trigger diet if overweight
        self._check_weight()

    def _check_weight(self):
        """Check habitat weight and trigger consolidation if overweight.

        Biological metaphor: body weight check. If the creature is overweight
        (habitat exceeds max_storage_mb), trigger a diet (memory consolidation).
        """
        if not self._config:
            return
        habitat_dir = self._config.get("habitat_dir", "")
        if not habitat_dir:
            return

        try:
            from ludex.core.habitat import HabitatConfig
            max_mb = self._config.get("max_storage_mb", 500)
            habitat_mode = self._config.get("habitat_mode", "temporary")
            if habitat_mode == "temporary":
                return  # no weight limit for ephemeral creatures

            # Measure current weight
            import os
            from pathlib import Path
            base = Path(habitat_dir)
            if not base.exists():
                return
            total_bytes = sum(f.stat().st_size for f in base.rglob("*") if f.is_file())
            total_mb = total_bytes / (1024 * 1024)
            usage_pct = (total_mb / max_mb * 100) if max_mb > 0 else 0

            if usage_pct > 90:
                # Critical: auto-consolidate
                logger.warning(f"Memory: habitat weight {total_mb:.1f}MB / {max_mb}MB ({usage_pct:.0f}%) — "
                             f"triggering automatic consolidation (diet)")
                self._emit("habitat.overweight", total_mb=total_mb, max_mb=max_mb, usage_pct=usage_pct)
                report = self.handle_consolidate()
                logger.info(f"Memory: diet complete — archived={report.archived}, "
                          f"promoted={report.promoted}, remaining={report.total_remaining}")
            elif usage_pct > 70:
                # Warning
                logger.info(f"Memory: habitat weight {total_mb:.1f}MB / {max_mb}MB ({usage_pct:.0f}%) — "
                          f"approaching limit, consider consolidation")
                self._emit("habitat.weight_warning", total_mb=total_mb, max_mb=max_mb, usage_pct=usage_pct)
        except Exception as e:
            logger.debug(f"Memory: weight check failed: {e}")

    def _periodic_weight_check(self, **kwargs):
        """Check habitat weight every N turns during runtime."""
        self._turns_since_weight_check += 1
        if self._turns_since_weight_check >= self._weight_check_interval:
            self._turns_since_weight_check = 0
            self._check_weight()

    def on_detach(self):
        # Skip bulk save on detach.
        # _save_incremental() already persists each new memory immediately,
        # and forget/consolidate call _save() explicitly.
        # Calling _save() during __del__ / interpreter shutdown causes
        # "name 'open' is not defined" because builtins are torn down.
        pass

    # --- Provides: remember ---

    def handle_remember(
        self,
        content: str,
        memory_type: str = "episodic",
        tags: list[str] | None = None,
        importance: float = 0.5,
        source: str = "",
        metadata: dict | None = None,
    ) -> str:
        """기억 저장. 반환: memory_id"""
        mem_id = f"mem_{self._next_id:04d}"
        self._next_id += 1

        from ludex.core.memory_types import estimate_tokens, MEMORY_TYPES
        if memory_type not in MEMORY_TYPES:
            logger.debug(f"Unknown memory_type '{memory_type}'; "
                         f"storing as-is (will decay at baseline rate)")

        memory = Memory(
            id=mem_id,
            content=content,
            memory_type=memory_type,
            tags=tags or [],
            importance=importance,
            source=source,
            token_count=estimate_tokens(content),
            metadata=metadata or {},
            protected=(memory_type == "identity"),  # D-071 Day 0 default
        )

        self._memories[mem_id] = memory
        self._save_incremental(memory)

        self._publish("memory.stored", {
            "id": mem_id, "type": memory_type, "importance": importance,
        })
        self._emit("memory.stored", memory_id=mem_id, memory_type=memory_type)

        # Vital signs 업데이트
        if self._config:
            self._config.set("_memory_entries", len(self._memories), layer="session")

        logger.debug(f"Remembered: [{memory_type}] {content[:50]}...")

        # Periodic weight check (every 50 memories added)
        if self._next_id % 50 == 0:
            self._check_weight()

        return mem_id

    # --- Provides: recall ---

    def handle_recall(self, query: str, memory_type: str | None = None, tags: list[str] | None = None, limit: int = 5, show_forgotten: bool = False) -> list[RecallResult]:
        """
        기억 검색. TF-IDF 기반 (Cody 피드백 #1 반영).
        벡터 검색은 Phase 6+.

        Tag convention: any tag starting with ``deprecated:`` (e.g.
        ``deprecated:lxm_leak``) marks a memory as kept-on-disk-but-
        excluded-from-recall. Use this for post-incident cleanups
        (D-067 bond-memory leak, 2026-04-30) where deletion would lose
        archaeological record but the memory must not resurface in
        future TF-IDF matches.

        D-071 pillar 3: ``forgotten=True`` memories are also excluded
        from the recall surface by default. Pass ``show_forgotten=True``
        to surface them (audit/diagnostic view). The substrate keeps
        the disk record either way — `forgotten` is a recall filter,
        not a deletion.
        """
        results = []

        query_lower = query.lower()
        query_words = set(query_lower.split())

        # TF-IDF: 문서 빈도 계산 (전체 기억에서 각 단어가 나타나는 기억 수).
        # Filters compose: status==active AND not deprecated:* AND (forgotten==False OR show_forgotten).
        active_memories = [
            m for m in self._memories.values()
            if m.status == "active"
            and not any(t.startswith("deprecated:") for t in (m.tags or []))
            and (show_forgotten or not m.forgotten)
        ]
        doc_freq: dict[str, int] = {}
        for mem in active_memories:
            words_in_doc = set(mem.content.lower().split())
            for w in words_in_doc:
                doc_freq[w] = doc_freq.get(w, 0) + 1
        n_docs = max(len(active_memories), 1)

        _recall_now = time.time()
        for memory in active_memories:
            if memory_type and memory.memory_type != memory_type:
                continue
            if tags and not any(t in memory.tags for t in tags):
                continue

            content_lower = memory.content.lower()
            content_words = set(content_lower.split())

            # TF-IDF 기반 관련도
            tfidf_score = 0.0
            for word in query_words:
                if word in content_words:
                    # TF: 쿼리 단어가 문서에 있으면 1
                    tf = 1.0
                    # IDF: log(N / df) — 희귀한 단어일수록 높음
                    df = doc_freq.get(word, 1)
                    idf = math.log(n_docs / df + 1)
                    tfidf_score += tf * idf

            # 정규화
            tfidf_normalized = tfidf_score / max(len(query_words), 1)

            # 쿼리가 내용에 포함되는지 (부분 문자열)
            substring_match = 1.0 if query_lower in content_lower else 0.0

            # 태그 매칭
            tag_match = 0.0
            if tags:
                tag_match = len(set(tags) & set(memory.tags)) / max(len(tags), 1)

            # 최종 관련도 (TF-IDF 중심)
            relevance = (
                tfidf_normalized * 0.5 +
                substring_match * 0.2 +
                tag_match * 0.2 +
                memory.importance * 0.1
            )

            # Recency-significance channel (2026-06-11, environment event —
            # the continuity-inequality fix): a creature must surface what
            # it RECENTLY lived through (field reflections, major events)
            # even when the query shares no tokens with the memory — a chat
            # in another language, a vague "어떻게 지냈어". Lexical recall
            # alone made continuity depend on query wording; the body must
            # not. Gated to SIGNIFICANT memories (importance ≥ 0.7) so
            # routine captures and default-importance notes neither ride it
            # nor enter the surfaced→importance-bump inflation loop the
            # recall-count tests guard. ~1-week decay: an importance-0.8
            # reflection clears the 0.15 gate on this channel alone for
            # ~10 days.
            if memory.importance >= 0.7:
                age_days = max((_recall_now - memory.created_at) / 86400.0, 0.0)
                relevance += memory.importance * math.exp(-age_days / 7.0) * 0.35

            if relevance > 0.15:
                results.append(RecallResult(memory=memory, relevance=relevance))

        # 관련도 순 정렬
        results.sort(key=lambda r: r.relevance, reverse=True)

        # Hit rate 업데이트
        if self._config:
            total_recalls = self._config.get("_memory_total_recalls", 0) + 1
            hits = self._config.get("_memory_hits", 0) + (1 if results else 0)
            self._config.set("_memory_total_recalls", total_recalls, layer="session")
            self._config.set("_memory_hits", hits, layer="session")
            self._config.set("_memory_hit_rate", hits / max(total_recalls, 1), layer="session")

        self._emit("memory.recalled", query=query, results_count=len(results[:limit]))

        limited = results[:limit]
        # D-071 pillar 4 — bump recall_count + importance for memories that
        # surface in the limited result set. "Often-recalled = important"
        # per design L4537-4538: recall_count tracks usage; importance
        # gets +0.05 capped at 0.95. The future forgetting pass reads
        # recall_count via compute_forget_score so frequently-recalled
        # memories naturally resist forgetting.
        recall_dirty = False
        for r in limited:
            r.memory.recall_count += 1
            new_importance = min(0.95, r.memory.importance + 0.05)
            if new_importance != r.memory.importance:
                r.memory.importance = new_importance
            recall_dirty = True
        if recall_dirty:
            self._save()

        self._last_recall = (query, list(limited))
        return limited

    @property
    def last_recall(self) -> tuple[str, list[RecallResult]] | None:
        """Most recent (query, results[:limit]) from `handle_recall`.

        Adapters that want to snapshot what Ludex surfaced for a given
        turn (e.g. LxM `ludex_state` per-turn log) should read this
        *after* `EngineBlock.handle_submit()` returns, instead of
        re-calling `handle_recall` (which re-runs TF-IDF, non-free).

        Returns None if no recall has been performed on this block yet.
        """
        return self._last_recall

    # --- Provides: recall_dual (D-085 param 4) ---

    def handle_recall_dual(self, query: str, limit: int = 5,
                           token_cap: int = 2000,
                           show_forgotten: bool = False) -> dict:
        """Dual-thread recall — D-085 param 4 invariant.

        When consolidation retrospectives exist (reflections/YYYY-MM.md),
        recall returns BOTH threads: the raw episodic memories AND the
        consolidated hindsight narrative. The contrast between in-the-
        moment record and consolidated retrospect is itself a driver of
        self-knowledge, so neither branch may be wholly starved by the
        token cap (agy size-bound refinement, 2026-05-30).

        Returns {"raw": [RecallResult...], "consolidated": [dict...]}
        where each consolidated hit is {"file", "section", "text", "score"}.
        With no reflections on disk this degrades to ordinary recall
        (consolidated == []) — ordinary recall is never bloated.
        """
        raw = self.handle_recall(query, limit=limit,
                                 show_forgotten=show_forgotten)
        consolidated = self.recall_consolidated(query, limit=limit)

        if not consolidated:
            return {"raw": raw, "consolidated": []}

        # Token-cap merge (≈4 chars/token). Each non-empty branch is
        # guaranteed at least its first hit, then remaining budget fills
        # by per-branch rank.
        budget = max(token_cap, 200) * 4
        raw_kept: list = []
        cons_kept: list = []
        if raw:
            raw_kept.append(raw[0])
            budget -= len(raw[0].memory.content)
        cons_kept.append(consolidated[0])
        budget -= len(consolidated[0]["text"])
        for branch, kept, size in (
            (raw[1:], raw_kept, lambda r: len(r.memory.content)),
            (consolidated[1:], cons_kept, lambda c: len(c["text"])),
        ):
            for item in branch:
                cost = size(item)
                if budget - cost < 0:
                    break
                kept.append(item)
                budget -= cost
        return {"raw": raw_kept, "consolidated": cons_kept}

    def recall_consolidated(self, query: str, limit: int = 5) -> list[dict]:
        """Rank sections of consolidation retrospectives against a query.

        Reads creatures/<name>/reflections/*.md via the habitat dir,
        splits each into `## ` sections, and scores by query-term overlap
        plus a small recency bonus (so the latest retrospective always
        participates even on zero term overlap — the invariant needs the
        consolidated thread present whenever one exists).
        """
        habitat_dir = self._config.get("habitat_dir", "") if self._config else ""
        if not habitat_dir:
            return []
        refl_dir = Path(habitat_dir) / "reflections"
        if not refl_dir.is_dir():
            return []

        query_words = set(query.lower().split())
        files = sorted(refl_dir.glob("*.md"))
        hits: list[dict] = []
        for rank_from_end, path in enumerate(reversed(files)):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            # Strip frontmatter.
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    text = text[end + 3:]
            recency_bonus = 0.1 / (1 + rank_from_end)
            for section in text.split("\n## "):
                body = section.strip()
                if not body:
                    continue
                words = set(body.lower().split())
                overlap = len(query_words & words) / max(len(query_words), 1)
                title = body.splitlines()[0].lstrip("# ").strip()
                hits.append({
                    "file": path.name,
                    "section": title,
                    "text": body,
                    "score": overlap + recency_bonus,
                })
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:limit]

    # --- Provides: forget ---

    def handle_forget(self, memory_id: str, hard_delete: bool = False) -> bool:
        """기억 삭제 또는 보관"""
        if memory_id not in self._memories:
            return False

        if hard_delete:
            del self._memories[memory_id]
        else:
            self._memories[memory_id].status = "archived"
            self._memories[memory_id].updated_at = time.time()

        self._save()
        return True

    # --- D-071 pillar 2: explicit protection API ---

    def handle_protect(self, memory_id: str, protected: bool = True) -> bool:
        """Mark/unmark a memory as protected from the forgetting pass
        (D-071 pillar 4). Useful for memories that weren't tagged
        `identity` at write time but later prove load-bearing (e.g.,
        a bond memory the creature self-pinned via reflection).
        Returns True if the memory exists and was updated."""
        if memory_id not in self._memories:
            return False
        m = self._memories[memory_id]
        if m.protected == protected:
            return True  # already in desired state; idempotent
        m.protected = protected
        m.updated_at = time.time()
        self._save()
        return True

    # --- D-071 pillar 4b: forgetting pass ---

    def run_forgetting_pass(
        self,
        reason: str = "score_threshold",
        *,
        target: int | None = None,
        provider: str = "",
        model: str = "",
        now: float | None = None,
    ) -> dict:
        """The actual D-071 forgetting pass. Closes the design.

        Computes `compute_forget_score(importance, age_days, recall_count)`
        for every forgettable memory (`is_forgettable=True` AND not
        already `forgotten`), sorts descending, and marks the top entries
        as forgotten until total active-recallable count is at or below
        `target`. Idempotent: re-running with the same target after
        a complete pass forgets 0 more.

        `target` resolution:
        - explicit `target` argument wins
        - else `get_disk_target(provider, model)` from BRAIN_DISK_TARGETS
        - this lets callers (heartbeat, post-match hook) wire either way

        `reason` is the slug stamped on each `handle_mark_forgotten`
        call. Canonical slugs per the D-071 design:
        `tier_pruned`, `score_threshold`, `disk_threshold`,
        `superseded_by_distillation`. `score_threshold` is the
        default (the most common trigger path).

        Returns a report dict with:
        - `total_before`: total memories on disk before
        - `forgettable`: count of candidates (forgettable + not already forgotten)
        - `target`: the budget used
        - `forgotten_now`: count newly marked forgotten this pass
        - `forgotten_ids`: list of IDs marked (newest-forgotten first)
        - `recallable_after`: post-pass recallable surface size

        No-ops (returns 0 forgotten) when count already under target,
        or when all candidates are protected. Protected entries are
        never touched."""
        from ludex.core.consolidation import compute_forget_score, get_disk_target

        if target is None:
            target = get_disk_target(provider, model)

        # Snapshot before. `recallable` = the surface a future
        # `handle_recall` would consider — active, not forgotten, not
        # deprecated-tagged. Includes protected entries (they show up
        # in recall, they just can't be forgotten). `candidates` is
        # the subset that's actually eligible to be forgotten.
        def _is_recallable(m) -> bool:
            return (
                m.status == "active"
                and not m.forgotten
                and not any(t.startswith("deprecated:") for t in (m.tags or []))
            )
        recallable = [m for m in self._memories.values() if _is_recallable(m)]
        candidates = [m for m in recallable if not m.protected]
        total_before = len([m for m in self._memories.values() if m.status == "active"])
        recallable_before = len(recallable)

        report = {
            "total_before": total_before,
            "forgettable": len(candidates),
            "target": target,
            "forgotten_now": 0,
            "forgotten_ids": [],
            "recallable_after": recallable_before,
        }

        if recallable_before <= target:
            return report  # already under budget, nothing to do

        ts = now if now is not None else time.time()
        # Score and sort highest-first (most forgettable on top).
        scored = []
        for m in candidates:
            age_days = max((ts - m.created_at) / 86400.0, 0.0)
            score = compute_forget_score(
                importance=m.importance,
                age_days=age_days,
                recall_count=m.recall_count,
            )
            scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Cap by candidate pool — if all unprotected don't get us under
        # target, we just forget all of them rather than touching protected.
        n_to_forget = min(recallable_before - target, len(candidates))
        forgotten_ids: list[str] = []
        for score, m in scored:
            if len(forgotten_ids) >= n_to_forget:
                break
            if self.handle_mark_forgotten(m.id, reason):
                forgotten_ids.append(m.id)

        report["forgotten_now"] = len(forgotten_ids)
        report["forgotten_ids"] = forgotten_ids
        report["recallable_after"] = recallable_before - len(forgotten_ids)
        logger.info(
            "D-071 forgetting pass: target=%d, recallable %d→%d, forgot %d (reason=%s)",
            target, recallable_before, report["recallable_after"], len(forgotten_ids), reason,
        )
        return report

    # --- D-071 pillar 3: retrieval-failure surface ---

    def handle_mark_forgotten(self, memory_id: str, reason: str) -> bool:
        """Mark a memory as forgotten — the trace fades but disk record
        persists. `handle_recall` excludes forgotten memories by default;
        pass `show_forgotten=True` for audit views. Refuses to forget
        protected memories (D-044 + D-071 pillar 2 invariant).

        `reason` is a short slug for observability — examples per the
        D-071 design: `tier_pruned`, `score_threshold`, `disk_threshold`,
        `superseded_by_distillation`, `manual`. No enum constraint at
        this layer — pillar 4 forgetting pass will populate canonical
        slugs; manual callers can pass any value.

        Returns True if the memory exists, is unprotected, and was
        marked. Idempotent on already-forgotten entries (returns True
        without changing fields)."""
        if memory_id not in self._memories:
            return False
        m = self._memories[memory_id]
        if m.protected:
            logger.warning(
                "handle_mark_forgotten refused: %s is protected (memory_type=%s)",
                memory_id, m.memory_type,
            )
            return False
        if m.forgotten:
            return True  # idempotent
        m.forgotten = True
        m.forgotten_at = time.time()
        m.forgotten_reason = reason or "unspecified"
        m.updated_at = m.forgotten_at
        self._save()
        # D-071 design §"Span emission for observability" attrs.
        self._emit(
            "memory.forgot",
            entry_id=memory_id,
            kind=m.memory_type,
            age_days=max((m.forgotten_at - m.created_at) / 86400.0, 0.0),
            importance=m.importance,
            forgotten_reason=m.forgotten_reason,
            tags=list(m.tags or []),
        )
        return True

    def list_forgotten(self) -> list[Memory]:
        """Return all memories with forgotten=True, sorted newest-forgotten
        first. Use with `handle_recall(query, show_forgotten=True)` to
        diff against current recall surface — useful for the audit /
        `--show-forgotten` diagnostic view."""
        forgotten = [m for m in self._memories.values() if m.forgotten]
        forgotten.sort(key=lambda m: m.forgotten_at, reverse=True)
        return forgotten

    def protect_legacy_identity_memories(self) -> int:
        """One-time backfill for D-071 Day 0: identity-type writes after
        2026-04-28 (`b3da347`) default to `protected=True`, but pre-Day-0
        identity entries loaded with `protected=False`. Call this once
        per creature at upgrade time to bring the historical record
        in line with the established invariant. Idempotent — safe to
        re-run. Returns the number of entries promoted to protected."""
        count = 0
        for m in self._memories.values():
            if m.memory_type == "identity" and not m.protected:
                m.protected = True
                m.updated_at = time.time()
                count += 1
        if count:
            self._save()
            logger.info(
                "D-071 pillar 2 backfill: protected %d legacy identity memories",
                count,
            )
        return count

    # --- Provides: consolidate (Dream) ---

    def handle_consolidate(self) -> ConsolidationReport:
        """Dream cycle — D-024 Sprint 1 substrate.

        Phase 1 (retained from Sprint 0):
          - Same-tag episodic clustering → belief/semantic promotion
          - Completed prospective → archive

        Phase 1 (revised):
          - Age-based archive now uses *effective age* per memory_type
            (identity 10× slower, transient 2× faster). A belief memory
            at age 7 days is only 1.4 effective-days old; an identity
            memory at age 70 days is only 7 effective-days old.
          - Budget-aware: when HOT token budget is exceeded, archive
            oldest-effective-age HOT memories until under budget.

        Phase 2 (new — mark-and-signal):
          - When WARM token budget is exceeded, select the oldest-
            effective-age WARM memories that together bring the tier
            back under budget. Mark them `candidate_for_distillation`.
          - Emit `memory.distillation_candidate` signal + span. Does
            NOT rewrite SELF.md (D-044: creature authors own changes).
            Sprint 2 will wire this signal to a reflect() trigger.

        COLD (identity) is never capped, never archived by the cycle.
        """
        from ludex.core.memory_types import (
            effective_age, tier_for_type, capacity_for, estimate_tokens,
        )

        promoted = 0
        archived = 0
        deleted = 0
        contradictions = 0
        distillation_candidates: list[Memory] = []

        now = time.time()
        seven_days = 7 * 24 * 3600

        # --- Phase 0: backfill token_count for legacy memories ---
        # Pre-Sprint-1 JSONL has no token_count field; handle_remember
        # only estimates at save time. Without backfill, budget_compliance
        # passes vacuously for every migrated creature.
        backfilled = 0
        for mem in self._memories.values():
            if mem.token_count == 0 and mem.content:
                mem.token_count = estimate_tokens(mem.content)
                backfilled += 1
        if backfilled:
            logger.info(f"Backfilled token_count for {backfilled} legacy memories")

        # Compute effective-age threshold: a "mature" memory is
        # effective_age > 7 days. Identity types therefore need raw
        # age > 70 days; transient types qualify at raw age > 3.5 days.
        def _mature(mem: Memory) -> bool:
            return effective_age(now - mem.created_at, mem.memory_type) > seven_days

        # --- Phase 1a: age-based archive for HOT memories ---
        for mem in list(self._memories.values()):
            if mem.status != "active":
                continue
            if tier_for_type(mem.memory_type) != "hot":
                continue
            if _mature(mem) and mem.importance < 0.3:
                mem.status = "archived"
                mem.updated_at = now
                archived += 1

        # --- Phase 1b: completed prospective → archive ---
        for mem in self._memories.values():
            if (mem.status == "active"
                    and mem.memory_type == "prospective"
                    and mem.metadata.get("completed", False)):
                mem.status = "archived"
                mem.updated_at = now
                archived += 1

        # --- Phase 1c: budget-aware HOT archive ---
        brain = self._resolve_brain()
        caps = capacity_for(brain)
        hot_budget = caps["hot_tokens"]
        warm_budget = caps["warm_tokens"]

        def _active_by_tier(tier: str) -> list[Memory]:
            return [m for m in self._memories.values()
                    if m.status == "active" and tier_for_type(m.memory_type) == tier]

        def _total_tokens(mems: list[Memory]) -> int:
            return sum((m.token_count or 0) for m in mems)

        hot = _active_by_tier("hot")
        while _total_tokens(hot) > hot_budget and hot:
            # Archive the oldest-effective-age HOT memory
            oldest = max(
                hot, key=lambda m: effective_age(now - m.created_at, m.memory_type)
            )
            oldest.status = "archived"
            oldest.updated_at = now
            archived += 1
            hot = _active_by_tier("hot")

        # --- Phase 1d: same-tag clustering → belief promotion ---
        # Clustered episodic events become `belief`. Idempotent:
        # skip tags that already have an active "consolidated"
        # belief memory (otherwise repeated consolidate runs produce
        # near-duplicate beliefs — D-024 Sprint 3 bug found 2026-04-16
        # during population-wide consolidate).
        tag_groups: dict[str, list[Memory]] = {}
        already_consolidated_tags: set[str] = set()
        for mem in self._memories.values():
            if mem.status != "active":
                continue
            if mem.memory_type == "episodic":
                for tag in mem.tags:
                    tag_groups.setdefault(tag, []).append(mem)
            elif mem.memory_type == "belief" and "consolidated" in (mem.tags or []):
                for tag in mem.tags:
                    if tag != "consolidated":
                        already_consolidated_tags.add(tag)

        for tag, mems in tag_groups.items():
            if len(mems) < 3:
                continue
            if tag in already_consolidated_tags:
                continue
            contents = [m.content for m in mems[:5]]
            summary = f"[Consolidated from {len(mems)} episodes] " + " | ".join(
                c[:50] for c in contents
            )
            self.handle_remember(
                content=summary,
                memory_type="belief",
                tags=[tag, "consolidated"],
                importance=0.7,
                source="consolidation",
            )
            promoted += 1

        # --- Phase 2: mark-and-signal for WARM over budget ---
        warm = _active_by_tier("warm")
        if _total_tokens(warm) > warm_budget:
            # Select oldest-effective-age warm memories until under budget
            warm_sorted = sorted(
                warm,
                key=lambda m: effective_age(now - m.created_at, m.memory_type),
                reverse=True,  # oldest effective-age first
            )
            overshoot = _total_tokens(warm) - warm_budget
            freed = 0
            for mem in warm_sorted:
                if freed >= overshoot:
                    break
                mem.status = "candidate_for_distillation"
                mem.updated_at = now
                distillation_candidates.append(mem)
                freed += mem.token_count or 0

        self._save()

        # Signal + span for Phase 2 candidates
        if distillation_candidates:
            try:
                self._publish("memory.distillation_candidate", {
                    "count": len(distillation_candidates),
                    "token_total": sum(
                        m.token_count or 0 for m in distillation_candidates
                    ),
                    "sample_ids": [m.id for m in distillation_candidates[:5]],
                })
            except Exception:
                pass
            try:
                from ludex.core import trace as _tr
                _tr.emit_memory_distillation_candidate(
                    self._organism,
                    count=len(distillation_candidates),
                    token_total=sum(
                        m.token_count or 0 for m in distillation_candidates
                    ),
                    sample_ids=[m.id for m in distillation_candidates[:5]],
                )
            except Exception:
                pass

        total = sum(1 for m in self._memories.values() if m.status == "active")
        report = ConsolidationReport(
            promoted=promoted,
            archived=archived,
            deleted=deleted,
            contradictions=contradictions,
            total_remaining=total,
        )

        self._emit("memory.consolidated",
                   promoted=promoted, archived=archived,
                   distillation_candidates=len(distillation_candidates))
        logger.info(
            f"Consolidation: +{promoted} promoted, -{archived} archived, "
            f"{len(distillation_candidates)} distillation candidates, "
            f"{total} remaining"
        )
        self._last_consolidated_at = now
        self._save_state()

        return report

    # --- Provides: health_check (D-024 Sprint 2) ---

    def handle_health_check(self) -> HealthReport:
        """Five assertions, pass rate + A/B/C/D grade. Run this
        periodically as a hygiene audit; output is safe to log and
        include in ethnography reports.
        """
        from ludex.core.memory_types import tier_for_type, capacity_for
        now = time.time()
        active = [m for m in self._memories.values() if m.status == "active"]
        notes: list[str] = []

        # 1. Source attribution — ≥ 80% have non-empty source
        if active:
            with_source = sum(1 for m in active if m.source)
            source_coverage = with_source / len(active)
        else:
            source_coverage = 1.0  # vacuously ok
        source_attribution = source_coverage >= 0.80
        if not source_attribution:
            notes.append(
                f"source_attribution: only {source_coverage:.0%} of "
                f"{len(active)} active memories have source"
            )

        # 2. Budget compliance — HOT/WARM under caps
        brain = self._resolve_brain()
        caps = capacity_for(brain)
        hot_tokens = sum(
            (m.token_count or 0) for m in active
            if tier_for_type(m.memory_type) == "hot"
        )
        warm_tokens = sum(
            (m.token_count or 0) for m in active
            if tier_for_type(m.memory_type) == "warm"
        )
        budget_compliance = (
            hot_tokens <= caps["hot_tokens"]
            and warm_tokens <= caps["warm_tokens"]
        )
        if not budget_compliance:
            notes.append(
                f"budget_compliance: HOT {hot_tokens}/{caps['hot_tokens']}, "
                f"WARM {warm_tokens}/{caps['warm_tokens']}"
            )

        # 3. Identity grounded — at least one active identity memory
        identity_count = sum(1 for m in active if m.memory_type == "identity")
        identity_grounded = identity_count >= 1
        if not identity_grounded:
            notes.append(
                "identity_grounded: no active identity memories — "
                "creature has no explicit self-grounding"
            )

        # 4. No duplicates — near-duplicate detection via normalized-content hash
        dup_count = self._count_near_duplicates(active)
        no_duplicates = dup_count == 0
        if not no_duplicates:
            notes.append(f"no_duplicates: {dup_count} near-duplicate pair(s) found")

        # 5. Consolidation fresh — last consolidate < 7 days ago
        seven_days = 7 * 24 * 3600
        if self._last_consolidated_at == 0:
            days_since = 999.0   # never consolidated
            consolidation_fresh = len(active) < 20  # below threshold, fine
        else:
            secs = now - self._last_consolidated_at
            days_since = secs / 86400
            consolidation_fresh = secs < seven_days
        if not consolidation_fresh and len(active) >= 20:
            notes.append(
                f"consolidation_fresh: {days_since:.1f} days since last "
                f"consolidate (threshold 7 days)"
            )

        return HealthReport(
            source_attribution=source_attribution,
            budget_compliance=budget_compliance,
            identity_grounded=identity_grounded,
            no_duplicates=no_duplicates,
            consolidation_fresh=consolidation_fresh,
            source_coverage=source_coverage,
            hot_tokens=hot_tokens,
            hot_budget=caps["hot_tokens"],
            warm_tokens=warm_tokens,
            warm_budget=caps["warm_tokens"],
            duplicate_count=dup_count,
            days_since_consolidation=days_since,
            notes=notes,
        )

    def _resolve_brain(self) -> dict:
        """Build a brain dict for tier_of / capacity_for lookups.

        OrganismConfig.build() stores brain as flat `model` +
        `provider` keys at the Organism config level, not a nested
        `brain` dict. Accept both shapes.
        """
        if not self._config:
            return {}
        nested = self._config.get("brain")
        if isinstance(nested, dict) and nested:
            return nested
        return {
            "model": self._config.get("model", "") or "",
            "provider": self._config.get("provider", "") or "",
        }

    @staticmethod
    def _count_near_duplicates(memories: list[Memory]) -> int:
        """Count near-duplicate pairs via normalized-content hash.
        Normalization: lowercase, strip punctuation, collapse whitespace.
        """
        import re
        seen: dict[str, list[str]] = {}
        for m in memories:
            norm = re.sub(r"[^a-z0-9 ]+", " ", m.content.lower())
            norm = re.sub(r"\s+", " ", norm).strip()
            # Hash a prefix so near-duplicates cluster; full hash would
            # miss common intros and differing suffixes
            key = norm[:120]
            if not key:
                continue
            seen.setdefault(key, []).append(m.id)
        return sum(1 for ids in seen.values() if len(ids) > 1)

    # --- Provides: list_memories ---

    def handle_list_memories(
        self,
        memory_type: str | None = None,
        tags: list[str] | None = None,
        status: str = "active",
        limit: int = 50,
    ) -> list[dict]:
        """기억 목록 조회"""
        results = []
        for mem in self._memories.values():
            if mem.status != status:
                continue
            if memory_type and mem.memory_type != memory_type:
                continue
            if tags and not any(t in mem.tags for t in tags):
                continue
            results.append(mem.to_dict())

        results.sort(key=lambda m: m["updated_at"], reverse=True)
        return results[:limit]

    # --- Auto-capture ---

    def _auto_capture_turn(self, turn_number: int = 0, success: bool = True, **kwargs):
        """턴 완료 신호 처리.

        D-024 Sprint 3 (2026-04-16): turn-boundary events are
        operational telemetry, not lived experience. They belong in
        the span store (D-028, KIND_TICK-family for field ticks,
        tracking block for engine turns), not in creature memory.
        Roster health survey (2026-04-16) found these had been
        dominating 44-72% of active memory across the population,
        with no code actually reading them.

        This handler now only records failures at error-level as
        transient memory (for post-hoc diagnosis). Successful turns
        are no-ops — tracking/tracing blocks already record them.
        """
        if success:
            return
        model = self._cfg("model", "unknown")
        self.handle_remember(
            content=f"Turn {turn_number}: error (model: {model})",
            memory_type="transient",
            tags=["turn", model, "error"],
            importance=0.4,
            source="auto_capture/error",
            metadata={"turn": turn_number, "success": False},
        )

    # --- Persistence (JSONL) ---

    def _get_storage_path(self) -> Path:
        path = Path(self._storage_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path / "memories.jsonl"

    def _get_state_path(self) -> Path:
        """D-024 Sprint 3: sidecar state file for ephemeral-instance-
        persistent fields (currently just _last_consolidated_at, used
        by handle_health_check's consolidation_fresh assertion).
        """
        return Path(self._storage_dir) / ".memory_state.json"

    def _save_state(self):
        try:
            p = self._get_state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({
                "last_consolidated_at": self._last_consolidated_at,
            }), encoding="utf-8")
        except Exception as e:
            logger.debug(f"memory state save failed: {e}")

    def _load_state(self):
        try:
            p = self._get_state_path()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                self._last_consolidated_at = float(
                    data.get("last_consolidated_at", 0.0)
                )
        except Exception as e:
            logger.debug(f"memory state load failed: {e}")

    def _load(self):
        """파일에서 기억 로드"""
        self._load_state()
        filepath = self._get_storage_path()
        if not filepath.exists():
            self._loaded = True
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    mem = Memory.from_dict(data)
                    self._memories[mem.id] = mem
                    # ID 카운터 업데이트
                    num = int(mem.id.split("_")[1])
                    if num >= self._next_id:
                        self._next_id = num + 1

            self._loaded = True
            logger.info(f"Loaded {len(self._memories)} memories from {filepath}")
        except Exception as e:
            logger.error(f"Failed to load memories: {e}")

    def _save(self):
        """전체 기억을 파일에 저장"""
        # Capture builtins as locals to survive interpreter shutdown
        _open = open
        _json = json
        try:
            filepath = self._get_storage_path()
            with _open(filepath, "w", encoding="utf-8") as f:
                for mem in self._memories.values():
                    f.write(_json.dumps(mem.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            try:
                logger.error(f"Failed to save memories: {e}")
            except Exception:
                pass

    def _save_incremental(self, memory: Memory):
        """새 기억을 파일에 추가 (append)"""
        _open = open
        _json = json
        try:
            filepath = self._get_storage_path()
            with _open(filepath, "a", encoding="utf-8") as f:
                f.write(_json.dumps(memory.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            try:
                logger.error(f"Failed to append memory: {e}")
            except Exception:
                pass

    # --- Stats ---

    @property
    def count(self) -> int:
        return sum(1 for m in self._memories.values() if m.status == "active")

    def stats(self) -> dict:
        active = [m for m in self._memories.values() if m.status == "active"]
        return {
            "total": len(active),
            "episodic": sum(1 for m in active if m.memory_type == "episodic"),
            "semantic": sum(1 for m in active if m.memory_type == "semantic"),
            "prospective": sum(1 for m in active if m.memory_type == "prospective"),
            "storage_dir": self._storage_dir,
        }
