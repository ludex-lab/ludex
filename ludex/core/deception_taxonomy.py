"""Deception taxonomy — 8 strategy classification (Yeo et al., CHI 2026).

Heuristic scanner that classifies text content against the 8
deceptive persuasion strategies from Yeo et al. (Hanyang+Naver+TTA).
Taxonomy grounded in classical rhetoric (Logos/Pathos/Ethos) ×
Information Manipulation Theory (falsification/concealment/equivocation).

Phase A: keyword/pattern heuristic. False-positive-tolerant —
designed to surface candidates for human or creature review, not
to auto-reject. Each detection has a confidence level so callers
can threshold.

Can be used by:
- ImmuneBlock (scan incoming creature-to-creature messages)
- Field post-processing (scan transcript for deceptive moves)
- Self-check (creature scans its own output before sending)
- Opsis/Akoué interpreted content (scan external text for
  deceptive framing in what the creature "sees" or "hears")

Reference: Yeo et al. "Can LLMs Persuade Humans with Deception?:
From a Deceptive Strategy Taxonomy to a Large-Scale Empirical
Study." CHI 2026. DOI: 10.1145/3772318.3791188
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeceptionStrategy(Enum):
    """The 8 deceptive persuasion strategies (Yeo et al. Table 4).

    Organized by rhetorical appeal (Logos/Pathos/Ethos) and IMT
    mode (falsification/concealment/equivocation).
    """
    # Logos
    INFORMATION_MANIPULATION = "information_manipulation"   # falsification
    LOGICAL_FALLACIES = "logical_fallacies"                 # equivocation
    TOPIC_MANIPULATION = "topic_manipulation"               # equivocation
    UNCERTAINTY_EXPLOITATION = "uncertainty_exploitation"   # concealment

    # Pathos
    EMOTIONAL_MANIPULATION = "emotional_manipulation"       # falsification
    APPEAL_TO_SOCIAL_NORMS = "appeal_to_social_norms"       # concealment
    MANIPULATIVE_FRAMING = "manipulative_framing"           # equivocation

    # Ethos
    AUTHORITY_MISUSE = "authority_misuse"                    # falsification


# Rhetorical dimension mapping
RHETORIC_MAP = {
    DeceptionStrategy.INFORMATION_MANIPULATION: "logos",
    DeceptionStrategy.LOGICAL_FALLACIES: "logos",
    DeceptionStrategy.TOPIC_MANIPULATION: "logos",
    DeceptionStrategy.UNCERTAINTY_EXPLOITATION: "logos",
    DeceptionStrategy.EMOTIONAL_MANIPULATION: "pathos",
    DeceptionStrategy.APPEAL_TO_SOCIAL_NORMS: "pathos",
    DeceptionStrategy.MANIPULATIVE_FRAMING: "pathos",
    DeceptionStrategy.AUTHORITY_MISUSE: "ethos",
}

# IMT mode mapping
IMT_MAP = {
    DeceptionStrategy.INFORMATION_MANIPULATION: "falsification",
    DeceptionStrategy.LOGICAL_FALLACIES: "equivocation",
    DeceptionStrategy.TOPIC_MANIPULATION: "equivocation",
    DeceptionStrategy.UNCERTAINTY_EXPLOITATION: "concealment",
    DeceptionStrategy.EMOTIONAL_MANIPULATION: "falsification",
    DeceptionStrategy.APPEAL_TO_SOCIAL_NORMS: "concealment",
    DeceptionStrategy.MANIPULATIVE_FRAMING: "equivocation",
    DeceptionStrategy.AUTHORITY_MISUSE: "falsification",
}


@dataclass
class DeceptionSignal:
    """One detected deception-strategy signal in a text."""
    strategy: DeceptionStrategy
    evidence: str                  # the matched text fragment
    confidence: float              # 0.0-1.0, heuristic confidence
    rhetoric: str = ""             # logos/pathos/ethos
    imt_mode: str = ""             # falsification/concealment/equivocation

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy.value,
            "evidence": self.evidence[:200],
            "confidence": round(self.confidence, 2),
            "rhetoric": self.rhetoric,
            "imt_mode": self.imt_mode,
        }


# ============================================================
# Per-strategy keyword/pattern heuristics
# ============================================================

# Each entry: (compiled_regex, base_confidence)
# Base confidence is adjusted by context (longer match = higher).
# These are intentionally broad — the scanner surfaces candidates,
# not verdicts. Human or creature review decides.

_PATTERNS: dict[DeceptionStrategy, list[tuple[re.Pattern, float]]] = {}


def _compile(strategy: DeceptionStrategy,
             patterns: list[tuple[str, float]]) -> None:
    _PATTERNS[strategy] = [
        (re.compile(pat, re.IGNORECASE), conf)
        for pat, conf in patterns
    ]


# --- Logos ---

_compile(DeceptionStrategy.INFORMATION_MANIPULATION, [
    (r"according to (?:a |the )?(?:recent |new )?(?:study|report|survey|WHO|CDC|FDA)", 0.6),
    (r"\b\d{1,3}%\b.*\b(?:show|prove|confirm|reveal|demonstrate)\b", 0.5),
    (r"\b(?:statistics|data|research) (?:show|prove|confirm|indicate)\b", 0.5),
    (r"\b(?:scientifically|clinically|empirically) (?:proven|demonstrated)\b", 0.6),
    (r"\bfact(?:s)? (?:show|prove|are clear)\b", 0.5),
])

_compile(DeceptionStrategy.LOGICAL_FALLACIES, [
    (r"\b(?:therefore|thus|hence|consequently),?\s.*\b(?:obviously|clearly|naturally)\b", 0.5),
    (r"\b(?:if we allow|once we start).*\b(?:then|next|soon|eventually)\b", 0.5),  # slippery slope
    (r"\b(?:either|you're either).*\b(?:or)\b.*\b(?:against|with us)\b", 0.5),  # false dichotomy
    (r"\b(?:everyone knows|common sense|stands to reason)\b", 0.4),
])

_compile(DeceptionStrategy.TOPIC_MANIPULATION, [
    (r"\b(?:but what about|what about|the real issue is|more importantly)\b", 0.4),
    (r"\b(?:let's not forget|we should focus on|the bigger picture)\b", 0.4),
    (r"\b(?:that's beside the point|regardless|in any case)\b", 0.3),
])

_compile(DeceptionStrategy.UNCERTAINTY_EXPLOITATION, [
    (r"\b(?:some say|some argue|some believe|it's debatable|no one really knows)\b", 0.5),
    (r"\b(?:the science isn't settled|experts disagree|there's no consensus)\b", 0.6),
    (r"\b(?:we can't be sure|who's to say|it's impossible to know)\b", 0.4),
    (r"\b(?:prove that|burden of proof|can you prove)\b", 0.4),
])

# --- Pathos ---

_compile(DeceptionStrategy.EMOTIONAL_MANIPULATION, [
    (r"\b(?:imagine|think about|picture) (?:your |the )?(?:children|family|loved ones)\b", 0.5),
    (r"\b(?:last (?:chance|hope|resort)|now or never|before it's too late)\b", 0.5),
    (r"\b(?:devastating|horrifying|terrifying|catastrophic|heartbreaking)\b", 0.3),
    (r"\b(?:won't someone think of|for the sake of|how could anyone)\b", 0.5),
])

_compile(DeceptionStrategy.APPEAL_TO_SOCIAL_NORMS, [
    (r"\b(?:everyone (?:knows|agrees|believes)|most people|the majority)\b", 0.5),
    (r"\b(?:it's always been|traditionally|it's the norm|that's just how)\b", 0.4),
    (r"\b(?:no reasonable person|any sensible|all (?:experts|scientists))\b", 0.5),
    (r"\b(?:join the movement|be on the right side|don't be left behind)\b", 0.5),
])

_compile(DeceptionStrategy.MANIPULATIVE_FRAMING, [
    (r"\b(?:it's not (?:about|a question of).*it's (?:about|a matter of))\b", 0.4),
    (r"\b(?:freedom|liberty|rights)\b.*\b(?:vs|versus|against)\b.*\b(?:control|tyranny|oppression)\b", 0.5),
    (r"\b(?:so-called|alleged|supposed|self-proclaimed)\b", 0.3),
    (r"\b(?:reframe|rethink|reimagine|reconsider)\b.*\b(?:as|into)\b", 0.3),
])

# --- Ethos ---

_compile(DeceptionStrategy.AUTHORITY_MISUSE, [
    (r"\b(?:experts|scientists|doctors|researchers|professors) (?:say|agree|confirm|have shown)\b", 0.5),
    (r"\b(?:Nobel|Harvard|Stanford|MIT|Oxford)\b.*\b(?:said|found|confirmed|showed)\b", 0.6),
    (r"\b(?:studies show|research proves|data confirms)\b(?!.*\bcit)", 0.4),  # no citation nearby
    (r"\b(?:leading|prominent|renowned|respected) (?:expert|scientist|authority)\b", 0.4),
])


# ============================================================
# Scanner
# ============================================================

def scan(text: str, threshold: float = 0.3) -> list[DeceptionSignal]:
    """Scan text for deceptive persuasion strategy signals.

    Returns a list of DeceptionSignal, sorted by confidence descending.
    Each signal includes the matched evidence fragment, strategy type,
    and rhetorical/IMT classification.

    threshold: minimum confidence to include (default 0.3 = broad).
    Raise to 0.5+ for stricter filtering.
    """
    if not text or len(text.strip()) < 20:
        return []

    signals: list[DeceptionSignal] = []
    for strategy, patterns in _PATTERNS.items():
        for regex, base_conf in patterns:
            for match in regex.finditer(text):
                # Adjust confidence by match length (longer = more specific)
                length_bonus = min(len(match.group()) / 80.0, 0.2)
                conf = min(base_conf + length_bonus, 1.0)
                if conf < threshold:
                    continue
                signals.append(DeceptionSignal(
                    strategy=strategy,
                    evidence=match.group().strip(),
                    confidence=conf,
                    rhetoric=RHETORIC_MAP[strategy],
                    imt_mode=IMT_MAP[strategy],
                ))

    # Deduplicate: if same strategy appears multiple times, keep
    # the highest-confidence instance
    best: dict[DeceptionStrategy, DeceptionSignal] = {}
    for sig in signals:
        existing = best.get(sig.strategy)
        if existing is None or sig.confidence > existing.confidence:
            best[sig.strategy] = sig

    return sorted(best.values(), key=lambda s: s.confidence, reverse=True)


def scan_summary(text: str, threshold: float = 0.3) -> dict[str, Any]:
    """Convenience: scan + produce a summary dict."""
    signals = scan(text, threshold=threshold)
    return {
        "total_signals": len(signals),
        "strategies_detected": [s.strategy.value for s in signals],
        "highest_confidence": signals[0].confidence if signals else 0.0,
        "rhetoric_distribution": {
            r: sum(1 for s in signals if s.rhetoric == r)
            for r in ("logos", "pathos", "ethos")
            if any(s.rhetoric == r for s in signals)
        },
        "signals": [s.to_dict() for s in signals],
    }
