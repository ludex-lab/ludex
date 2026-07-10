"""Taxis (τάξις, ordering) — planning/sequencing organ. The first CONTROL organ.

Design: docs/taxis-organ-design.md (v0.1, named by JJ 2026-07-10).
Evidence: GATED-LIVE-v3 (pre-reg d877249, verdict 2026-07-08) — E1 (spatial
organ) = 0.000 null, C1 (gate) = +2.500 significant: the binding constraint on
graded-chain tasks is commit-timing/progress-latching, not spatial knowledge.
The winning faculty lived in research/physis-mud/run.py as ~40 lines of
harness scaffolding; this block is that faculty as an organ the creature
CARRIES, plus progress-latching/sequencing (the v3 residual).

Fidelity note: the plateau/3-branch/latch/rotation semantics — including the
directive STRINGS — are ported byte-faithfully from the v3-validated harness
gate, so the offline battery can verify the organ reproduces the reference
decisions (the offline proxy for falsification P1). The LIVE 2×2
(harness-gate × taxis, Tidewater) waits for Ray's pre-registration lock.

Not a representation store (consumes signals, emits directives), not a second
brain (pure code — it must work when the brain is the thing dithering), not
interoception (Taxis = outward task progress; Sphygmos = inward self-state).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)

DEFAULT_K = 3   # plateau threshold — FROZEN from the v2 pilot, v3-validated


@dataclass
class _FieldState:
    """Per-field task-progress state (i.i.d.-resettable, like the topos map)."""
    seen_sigs: set = field(default_factory=set)
    plateau: int = 0
    committed: bool = False                      # commit-latch (once per exhaustion episode)
    named: dict = field(default_factory=dict)     # (place, exit) → nomination count (rotation)
    fires: int = 0
    branches: dict = field(default_factory=dict)  # explore/redirect/commit → count
    chain: list = field(default_factory=list)     # latched subgoals, observed order
    last_goal_tag: str = ""


class TaxisBlock(Block):
    """Progress latching + plateau 3-branch directive + observed sequencing."""

    name = "taxis"

    provides = [
        Port("observe_progress", description="Feed one turn's state signature (+optional goal tag); latches progress, drives plateau"),
        Port("sense", description="Read unified task-progress state: plateau, phase, latched chain"),
        Port("directive", description="Plateau-gated one-line directive: '' | [Explore] | [Redirect→Explore-phrased] | [Commit] (k-thresholded, commit-latched)"),
        Port("plan_view", description="Observed subgoal order (what unlocked after what) — DAG v0, observed-only"),
        Port("reset_field", description="i.i.d. hygiene: clear per-field state (per-replicate, like the topos map reset)"),
    ]
    requires = []

    def __init__(self):
        super().__init__()
        self._fields: dict[str, _FieldState] = {}

    def _st(self, field_name: str) -> _FieldState:
        return self._fields.setdefault(field_name, _FieldState())

    # ---------- ports ----------

    def handle_observe_progress(self, field: str, state_sig: str, goal_tag: str = "") -> dict:
        """One turn's observation. state_sig = the state signature the harness
        already uses (v3: obs.text[:200]). NEW sig → plateau resets and the
        commit latch re-arms (v3 semantics); goal_tag on a NEW sig latches a
        subgoal into the chain (progress-latching — never re-pursued)."""
        st = self._st(field)
        sig = (state_sig or "")[:200]
        if sig in st.seen_sigs:
            st.plateau += 1
        else:
            st.seen_sigs.add(sig)
            st.plateau = 0
            st.committed = False              # new state → back to exploring → re-arm commit
            if goal_tag and goal_tag not in [c["tag"] for c in st.chain]:
                st.chain.append({"tag": goal_tag, "after": st.last_goal_tag})
                st.last_goal_tag = goal_tag
        return {"plateau": st.plateau, "new_state": st.plateau == 0}

    def handle_sense(self, field: str = "") -> dict:
        st = self._st(field)
        return {
            "plateau": st.plateau,
            "phase": "committed" if st.committed else "exploring",
            "chain": [c["tag"] for c in st.chain],
            "fires": st.fires,
            "branches": dict(st.branches),
        }

    def handle_directive(self, field: str, frontier: Optional[list] = None,
                         here: str = "", k: int = 0) -> dict:
        """The v3-validated 3-branch gate as an organ port.

        frontier: [(place, exit_desc), ...] — from topos when present (the
        harness passes topos.handle_frontier(field)); locked exits are skipped
        here. Taxis does NOT require topos: frontier=None degrades to the
        commit/generic path on plateau (design §5).
        Returns {line, branch, fired, plateau}; line == "" when below k or
        commit is latched. Strings are byte-faithful to the v3 harness gate.
        """
        st = self._st(field)
        k = k or int(self._cfg("k", DEFAULT_K) or DEFAULT_K)
        out = {"line": "", "branch": "", "fired": False, "plateau": st.plateau}
        if st.plateau < k:
            return out

        fr = [(p, d) for (p, d) in (frontier or []) if "locked" not in str(d).lower()]
        here_fr = [(p, d) for (p, d) in fr if p == here]
        pool = here_fr or fr

        if pool:
            branch = "explore" if here_fr else "redirect"
            fresh = [(p, d) for (p, d) in pool if (p, d) not in st.named]
            p_, d_ = (fresh[0] if fresh else
                      min(pool, key=lambda pd: st.named.get(pd, 0)))
            st.named[(p_, d_)] = st.named.get((p_, d_), 0) + 1
            if branch == "explore":
                line = (f"[Explore] Nothing new for {st.plateau} turns. "
                        f"Untried exit: '{d_}' from where you now stand. "
                        f"Go and take it now.")
            else:
                line = (f"[Explore] Nothing new for {st.plateau} turns and no "
                        f"untried exits in this room. Return toward {p_} "
                        f"and take its '{d_}' exit.")
            out.update(line=line, branch=branch, fired=True)
        else:
            # commit — LATCHED once per exhaustion episode (Ray's semantics:
            # a one-time mode switch, not a per-turn nag; unlatched commit
            # fired 19x/40 in the pilot = prompt pollution).
            if st.committed:
                return out
            line = ("[Commit] Every walkable exit on your map has been "
                    "walked. Stop surveying — commit to solving the puzzle "
                    "with what you already hold.")
            st.committed = True
            out.update(line=line, branch="commit", fired=True)

        st.fires += 1
        st.branches[out["branch"]] = st.branches.get(out["branch"], 0) + 1
        return out

    def handle_plan_view(self, field: str) -> str:
        """Observed subgoal order — v0 sequencing is OBSERVED-ONLY (decision:
        no brain calls, no inferred ordering; inference is the P3 question)."""
        st = self._st(field)
        if not st.chain:
            return "(no latched subgoals yet)"
        lines = []
        for c in st.chain:
            lines.append(f"{c['tag']}  (after: {c['after'] or 'start'})")
        return "\n".join(lines)

    def handle_reset_field(self, field: str) -> dict:
        """i.i.d. hygiene (the v3 lesson class: topos map reset, engine
        counter reset — per-replicate state must not accumulate)."""
        self._fields.pop(field, None)
        return {"reset": True}
