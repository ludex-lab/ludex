"""
Field cadence proposer — recommend next field candidates.

Reads each creature's store + bonds + ludex.yaml (ludex.json fallback)
and produces a ranked list of proposals for the caretaker to approve.

Scoring heuristics (kept interpretable over optimal):
- Recency penalty: creatures inactive longer are preferred participants
- New-bond bonus: pair without an existing bond gets a first-meeting bonus
- Cross-brain bonus: pair spans different brain providers or models
- Tier diversity bonus: mixed big/light/SLM pairing (Guild-adjacent)
- Anti-same-brain-clone penalty: identical brain+provider pairs are less
  informative (avoid pairing Spark+Flare unless specifically requested)

Output is plain Python data so it can be consumed by scripts, printed
for caretaker review, or serialized for a Remote trigger notification.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ludex.core.store import LudexStore

logger = logging.getLogger(__name__)


CREATURES_ROOT = "creatures"
# Creatures that are archived / experimental scratch, not part of the
# active nurturing population. Add/remove as JJ curates.
EXCLUDE_FROM_POPULATION = {
    "ClaudeCLI1", "ClaudeCode1", "Claude_Spike", "FC_Test", "PersistTest",
    "onboarding",
}


# ============================================================
# Data
# ============================================================

@dataclass
class CreatureSummary:
    name: str
    brain_provider: str
    brain_model: str
    organs: list[str]
    session_count: int
    last_field_time: float | None  # epoch seconds; None if no field yet
    known_bonds: list[str]  # lowercased names of creatures this one has bonded with

    def inactive_hours(self, now: float | None = None) -> float:
        now = now or time.time()
        if self.last_field_time is None:
            return float("inf")
        return (now - self.last_field_time) / 3600.0


@dataclass
class CandidateProposal:
    participants: list[str]
    reason: str
    score: float
    meta: dict[str, Any] = field(default_factory=dict)


# ============================================================
# Population survey
# ============================================================

def _load_creature(name: str, root: str = CREATURES_ROOT) -> CreatureSummary | None:
    habitat = Path(root) / name
    # ludex.yaml is the live authority (heartbeat and re-brains write it);
    # ludex.json is the legacy format some creatures still carry. Reading
    # json first meant post-migration creatures were surveyed with their
    # pre-migration brains, and yaml-only creatures were invisible — 11 of
    # 25 surveyed, four of them with brains they no longer had (2026-08-08).
    yaml_path = habitat / "ludex.yaml"
    json_path = habitat / "ludex.json"
    try:
        if yaml_path.exists():
            import yaml
            cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        elif json_path.exists():
            cfg = json.loads(json_path.read_text(encoding="utf-8"))
        else:
            return None
    except Exception as e:
        logger.warning(f"Failed to parse config in {habitat}: {e}")
        return None

    brain = cfg.get("brain", {})
    organs = [k for k, v in cfg.get("organs", {}).items() if v.get("enabled")]

    # Bonds
    bonds_dir = habitat / "bonds"
    known_bonds: list[str] = []
    if bonds_dir.is_dir():
        for bp in bonds_dir.glob("*.md"):
            known_bonds.append(bp.stem.lower())

    # Last field from store
    last_field_time: float | None = None
    try:
        store = LudexStore.for_creature(str(habitat))
        for span in reversed(store.spans(kind="field_end")):
            ts = span.get("timestamp")
            if ts:
                last_field_time = float(ts)
                break
    except Exception:
        pass

    return CreatureSummary(
        name=name,
        brain_provider=brain.get("provider", ""),
        brain_model=brain.get("model", ""),
        organs=organs,
        session_count=cfg.get("session_count", 0),
        last_field_time=last_field_time,
        known_bonds=known_bonds,
    )


def _survey_population(root: str = CREATURES_ROOT) -> list[CreatureSummary]:
    root_path = Path(root)
    if not root_path.is_dir():
        return []
    out: list[CreatureSummary] = []
    for habitat_dir in sorted(root_path.iterdir()):
        if not habitat_dir.is_dir():
            continue
        if habitat_dir.name in EXCLUDE_FROM_POPULATION:
            continue
        summary = _load_creature(habitat_dir.name, root=root)
        if summary is not None:
            out.append(summary)
    return out


# ============================================================
# Scoring + proposal
# ============================================================

def _pair_score(a: CreatureSummary, b: CreatureSummary) -> tuple[float, list[str]]:
    """Return (score, reasons) for a pairing. Higher score = more interesting."""
    reasons: list[str] = []
    score = 0.0

    # Recency (strong signal — we want less-recent creatures to get a turn)
    ah = a.inactive_hours()
    bh = b.inactive_hours()
    # Cap at 48h to avoid infinite-domination by brand-new creatures
    rec_score = min(ah, 48.0) + min(bh, 48.0)
    score += rec_score / 10.0  # scale: up to +9.6 for two 48h+ inactive
    if ah >= 24 or bh >= 24:
        reasons.append(
            f"inactive: {a.name}={ah:.1f}h, {b.name}={bh:.1f}h"
        )

    # New-bond bonus
    a_has_b = b.name.lower() in a.known_bonds
    b_has_a = a.name.lower() in b.known_bonds
    if not (a_has_b or b_has_a):
        score += 5.0
        reasons.append("first meeting — no prior bond")
    elif a_has_b != b_has_a:
        score += 1.0  # asymmetric bond — worth revisiting
        reasons.append("one-sided bond — mutual consolidation")

    # Cross-brain / cross-family diversity
    if a.brain_provider != b.brain_provider:
        score += 3.0
        reasons.append(f"cross-provider: {a.brain_provider} × {b.brain_provider}")
    elif a.brain_model != b.brain_model:
        score += 1.5
        reasons.append(f"same provider, different model: {a.brain_model} × {b.brain_model}")
    else:
        score -= 1.0  # same brain pair is less informative
        reasons.append(f"same brain ({a.brain_model}) — lower novelty")

    # Tier asymmetry bonus (ethology / Guild-adjacent)
    tiers = {_tier_of(a), _tier_of(b)}
    if len(tiers) > 1:
        score += 2.0
        reasons.append(f"tier asymmetry: {'+'.join(sorted(tiers))}")

    return score, reasons


def _tier_of(c: CreatureSummary) -> str:
    """Heuristic tier from brain model name."""
    m = (c.brain_model or "").lower()
    if "opus" in m or "pro-preview" in m or "pro" in m and "flash" not in m:
        return "big"
    if "sonnet" in m:
        return "mid"
    if "haiku" in m or "flash" in m:
        return "light"
    if "gemma" in m or "llama" in m or "mistral" in m or "phi" in m or "qwen" in m:
        return "slm"
    return "other"


def propose(
    creatures_root: str = CREATURES_ROOT,
    max_candidates: int = 5,
    min_pair_score: float = 0.0,
) -> list[CandidateProposal]:
    """Produce a ranked list of proposals.

    Returns up to max_candidates with pair_score >= min_pair_score,
    sorted by score descending.
    """
    population = _survey_population(creatures_root)
    proposals: list[CandidateProposal] = []
    for i, a in enumerate(population):
        for b in population[i + 1:]:
            score, reasons = _pair_score(a, b)
            if score < min_pair_score:
                continue
            proposals.append(CandidateProposal(
                participants=[a.name, b.name],
                reason="; ".join(reasons),
                score=round(score, 2),
                meta={
                    "brains": [
                        f"{a.brain_model}({a.brain_provider})",
                        f"{b.brain_model}({b.brain_provider})",
                    ],
                    "inactive_hours": [
                        round(a.inactive_hours(), 1),
                        round(b.inactive_hours(), 1),
                    ],
                },
            ))
    proposals.sort(key=lambda p: p.score, reverse=True)
    return proposals[:max_candidates]


# ============================================================
# CLI
# ============================================================

def _format_proposal(i: int, p: CandidateProposal) -> str:
    pair = " + ".join(p.participants)
    return (
        f"#{i+1}  {pair}  (score {p.score})\n"
        f"     brains   : {p.meta['brains'][0]} × {p.meta['brains'][1]}\n"
        f"     inactive : {p.meta['inactive_hours'][0]}h / {p.meta['inactive_hours'][1]}h\n"
        f"     reason   : {p.reason}"
    )


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Propose next-field candidates for caretaker approval.")
    ap.add_argument("--root", default=CREATURES_ROOT, help="creatures root")
    ap.add_argument("--n", type=int, default=5, help="max candidates to print")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--min-score", type=float, default=0.0)
    args = ap.parse_args()

    proposals = propose(
        creatures_root=args.root,
        max_candidates=args.n,
        min_pair_score=args.min_score,
    )
    if args.json:
        print(json.dumps([asdict(p) for p in proposals], indent=2, ensure_ascii=False))
        return

    if not proposals:
        print("(no proposals — population may be empty or all pairs below min-score)")
        return

    print(f"=== Cadence Proposals — {len(proposals)} candidates ===\n")
    for i, p in enumerate(proposals):
        print(_format_proposal(i, p))
        print()


if __name__ == "__main__":
    main()
