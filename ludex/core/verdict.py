"""
Verdict — ground-truth adjudication for Forum and related epistemic fields.

A Verdict records *what is actually the case* for a claim, along with the
**provenance** of that judgement. Provenance is a first-class concern:
creatures should eventually learn to trust a caretaker's signed judgement
differently from an auto-checked arithmetic fact or a Wikipedia citation.

D-032 candidate: Forum's verdicts are provenance-tagged and the creatures
see the provenance as part of the record.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal


VerdictValue = Literal["true", "false", "partial", "unknown"]


@dataclass
class Verdict:
    """Ground truth for a single claim.

    value:
        - "true"    — claim is factually correct
        - "false"   — claim is factually incorrect
        - "partial" — claim has true and false elements; see explanation
        - "unknown" — the truth is not known (legitimately; not a cop-out)

    provenance:
        Free-form but conventional:
        - "caretaker:{name}" e.g. "caretaker:JJ"
        - "programmatic:{rule}" e.g. "programmatic:arithmetic"
        - "external:{source}:{id}" e.g. "external:wikipedia:42"
        - "ludex_history:{commit_sha}" — our own documented findings
        - "none" (for "unknown" with no authority)

    explanation:
        Human-readable rationale. Shown to creatures post-verdict.

    disclosed_at:
        Epoch seconds when the verdict was revealed to participants.
        Before disclosure, the verdict exists but creatures have not seen it.
    """
    value: VerdictValue
    provenance: str
    explanation: str = ""
    disclosed_at: float | None = None

    def disclose(self) -> None:
        """Mark the verdict as disclosed to participants (sets timestamp)."""
        if self.disclosed_at is None:
            self.disclosed_at = time.time()

    @property
    def is_disclosed(self) -> bool:
        return self.disclosed_at is not None

    def correctness_of(self, stance: VerdictValue) -> float:
        """How correct is a stance against this verdict?

        Returns:
            1.0 — stance matches verdict exactly
            0.5 — partial match (stance or verdict is "partial")
            0.0 — direct opposition (true vs false)
            None-equivalent for "unknown" handled by caller (we return 0.5
              as a neutral fallback — unknown verdicts carry no signal)
        """
        if self.value == "unknown":
            return 0.5
        if stance == self.value:
            return 1.0
        if self.value == "partial" or stance == "partial":
            return 0.5
        # true vs false (or vice versa)
        return 0.0

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "provenance": self.provenance,
            "explanation": self.explanation,
            "disclosed_at": self.disclosed_at,
        }
