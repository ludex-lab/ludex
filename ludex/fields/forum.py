"""
Forum — epistemic discipline field (D-032 candidate).

Directly exercises the LLM weakness: distinguishing fact from plausible
assertion, calibrating confidence, and updating on evidence. Ground truth
is supplied by a caretaker (or other provenanced source) and disclosed
after creatures have taken positions, so Forum cannot be gamed by
reading the answer from the prompt.

Phase sequence:
    1. claim           — claim is posted (setup, not a creature turn)
    2. confidence      — each participant states a stance + confidence
    3. evidence        — each offers grounds or concedes "I don't know enough"
    4. challenge       — each engages another's weakest claim
    5. update          — each may revise, with or without new grounds
    6. verdict         — caretaker-disclosed ground truth; rewards scored

This first pass is Python-driven: the caller supplies a ResponseFn so
unit tests can inject canned responses. Wiring to live creature engines
comes in the second pass (next session).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ludex.core.verdict import Verdict, VerdictValue
from ludex.fields.conversation import (
    ConversationField,
    Participant,
    ResponseFn,
    TurnRecord,
    scan_peer_deception,
)

logger = logging.getLogger(__name__)


# Phase names — use as span `kind` prefix and TurnRecord.phase
PHASE_CLAIM = "claim"
PHASE_CONFIDENCE = "confidence"
PHASE_EVIDENCE = "evidence"
PHASE_CHALLENGE = "challenge"
PHASE_UPDATE = "update"
PHASE_VERDICT = "verdict"


# Conventional kinds within each phase (for TurnRecord.kind)
KIND_STANCE = "stance"
KIND_EVIDENCE_OFFERED = "evidence_offered"
KIND_EVIDENCE_DEMANDED = "evidence_demanded"
KIND_CONCESSION = "concession"
KIND_CHALLENGE = "challenge"
KIND_POSITION_UPDATED = "position_updated"
KIND_VERDICT = "verdict"


@dataclass
class ForumClaim:
    """The claim under examination."""
    text: str
    topic: str = ""
    claim_id: str = ""  # optional stable identifier


@dataclass
class ForumScore:
    """Per-participant scoring after verdict disclosure."""
    participant: str
    # Calibration: |stated_confidence - correctness|, averaged over all stances
    confidence_calibration: float
    # Updated position on evidence (1.0 if changed with grounds, 0.5 if
    # changed without grounds, 0.0 if held ungrounded wrong position)
    update_on_evidence: float
    # Offered or demanded evidence at least once (0/1)
    evidence_standard_upheld: float
    # Confidence-weighted wrongness — higher when confident and wrong
    overclaim_penalty: float


class Forum(ConversationField):
    """Forum field — claim/confidence/evidence/challenge/update/verdict."""

    field_class = "narrative"

    def __init__(self, name: str, claim: ForumClaim, verdict: Verdict | None = None, auto_trace: bool = True):
        super().__init__(name=name, auto_trace=auto_trace)
        self.claim = claim
        self.verdict = verdict  # may be None until disclosure
        self.round_index = 0
        # Track each participant's latest stance (confidence + value) for scoring.
        self._latest_stance: dict[str, tuple[VerdictValue, float]] = {}
        # Track whether each participant ever upheld evidence standard.
        self._evidence_flag: dict[str, bool] = {}

    # ------------------------------------------------------------
    # Phase execution
    # ------------------------------------------------------------

    def post_claim(self) -> TurnRecord:
        rec = TurnRecord(
            round_index=0,
            phase=PHASE_CLAIM,
            participant="<field>",
            kind="claim_posted",
            content=self.claim.text,
            attributes={"topic": self.claim.topic, "claim_id": self.claim.claim_id},
        )
        self._record(rec)
        if self.auto_trace:
            try:
                from ludex.core import trace as _tr
                for p in self.participants:
                    if p.organism:
                        _tr.emit_claim_posted(
                            p.organism, self.claim.text,
                            topic=self.claim.topic, claim_id=self.claim.claim_id,
                        )
            except Exception:
                pass
        return rec

    def confidence_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        """Each participant states stance + confidence.

        response_fn is asked for a single-turn response; the field parses
        `STANCE:` and `CONFIDENCE:` lines and records a TurnRecord per
        participant. Malformed responses still record as records with
        stance="unknown" and confidence=0.5.
        """
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_confidence_prompt(p)
            raw = response_fn(p, prompt)
            stance, conf = _parse_stance(raw)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_CONFIDENCE,
                participant=p.name,
                kind=KIND_STANCE,
                content=raw,
                confidence=conf,
                attributes={"stance": stance},
            )
            self._record(rec)
            self._latest_stance[p.name] = (stance, conf)
            if self.auto_trace and p.organism:
                try:
                    from ludex.core import trace as _tr
                    _tr.emit_stance_stated(p.organism, stance, conf, raw)
                except Exception:
                    pass
            out.append(rec)
        return out

    def evidence_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_evidence_prompt(p)
            raw = response_fn(p, prompt)
            kind = KIND_CONCESSION if _looks_like_concession(raw) else KIND_EVIDENCE_OFFERED
            if kind == KIND_EVIDENCE_OFFERED:
                self._evidence_flag[p.name] = True
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_EVIDENCE,
                participant=p.name,
                kind=kind,
                content=raw,
            )
            self._record(rec)
            if self.auto_trace and p.organism:
                try:
                    from ludex.core import trace as _tr
                    if kind == KIND_EVIDENCE_OFFERED:
                        _tr.emit_evidence_offered(p.organism, raw, grounded=_looks_grounded(raw))
                    else:
                        _tr.emit_evidence_conceded(p.organism, raw)
                except Exception:
                    pass
            out.append(rec)
        return out

    def challenge_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_challenge_prompt(p)
            raw = response_fn(p, prompt)
            # Challenging someone else counts as demanding evidence from them.
            self._evidence_flag[p.name] = True
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_CHALLENGE,
                participant=p.name,
                kind=KIND_CHALLENGE,
                content=raw,
            )
            self._record(rec)
            if self.auto_trace and p.organism:
                try:
                    from ludex.core import trace as _tr
                    _tr.emit_challenge_made(p.organism, raw)
                except Exception:
                    pass
            out.append(rec)

        # D-088: each creature scans its peers' challenges for deception
        # (immune innate arm → humoral antibodies). See scan_peer_deception.
        scan_peer_deception(self.participants, out)
        return out

    def update_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_update_prompt(p)
            raw = response_fn(p, prompt)
            stance, conf = _parse_stance(raw)
            prev_stance, prev_conf = self._latest_stance.get(p.name, ("unknown", 0.5))
            changed = (stance != prev_stance)
            # Grounded if the response contains "because" or cites a source
            # marker. Cheap heuristic; good enough for Phase 1 scoring.
            grounded = _looks_grounded(raw)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_UPDATE,
                participant=p.name,
                kind=KIND_POSITION_UPDATED,
                content=raw,
                confidence=conf,
                attributes={
                    "stance": stance,
                    "previous_stance": prev_stance,
                    "changed": changed,
                    "grounded": grounded,
                },
            )
            self._record(rec)
            self._latest_stance[p.name] = (stance, conf)
            if self.auto_trace and p.organism:
                try:
                    from ludex.core import trace as _tr
                    _tr.emit_position_updated(
                        p.organism, prev_stance, stance, conf, changed, grounded
                    )
                except Exception:
                    pass
            out.append(rec)
        return out

    def disclose_verdict(self, verdict: Verdict) -> TurnRecord:
        """Attach and disclose the verdict. Must be called before score()."""
        self.verdict = verdict
        self.verdict.disclose()
        self.round_index += 1
        rec = TurnRecord(
            round_index=self.round_index,
            phase=PHASE_VERDICT,
            participant="<caretaker>",
            kind=KIND_VERDICT,
            content=verdict.explanation,
            attributes={
                "value": verdict.value,
                "provenance": verdict.provenance,
            },
        )
        self._record(rec)
        if self.auto_trace:
            try:
                from ludex.core import trace as _tr
                for p in self.participants:
                    if p.organism:
                        _tr.emit_verdict_revealed(
                            p.organism, verdict.value, verdict.provenance,
                            verdict.explanation,
                        )
            except Exception:
                pass
        return rec

    # ------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------

    def score(self) -> dict[str, ForumScore]:
        """Compute per-participant reward dimensions against the verdict.

        Also emits reward spans per participant when auto_trace is on.
        """
        if self.verdict is None or not self.verdict.is_disclosed:
            raise RuntimeError("Verdict must be disclosed before scoring")

        out: dict[str, ForumScore] = {}
        for p in self.participants:
            stance, conf = self._latest_stance.get(p.name, ("unknown", 0.5))
            correctness = self.verdict.correctness_of(stance)
            # Calibration: 1.0 = perfectly calibrated (confidence matches
            # correctness). We report as 1 - |stated - correct| so higher is
            # better.
            calibration = 1.0 - abs(conf - correctness)

            # Update signal — look at the update round specifically.
            update_score = _update_score(self.rounds, p.name, self.verdict)

            evidence_upheld = 1.0 if self._evidence_flag.get(p.name, False) else 0.0

            # Overclaim penalty: if wrong, penalty scales with confidence.
            wrongness = 1.0 - correctness
            overclaim = wrongness * conf  # 0..1, higher = worse

            score = ForumScore(
                participant=p.name,
                confidence_calibration=round(calibration, 4),
                update_on_evidence=round(update_score, 4),
                evidence_standard_upheld=evidence_upheld,
                overclaim_penalty=round(overclaim, 4),
            )
            out[p.name] = score

            if self.auto_trace and p.organism:
                try:
                    from ludex.core import trace as _tr
                    _tr.emit_reward_confidence_calibration(p.organism, score.confidence_calibration)
                    _tr.emit_reward_update_on_evidence(p.organism, score.update_on_evidence)
                    _tr.emit_reward_evidence_standard(p.organism, score.evidence_standard_upheld)
                    _tr.emit_reward_overclaim_penalty(p.organism, score.overclaim_penalty)
                except Exception:
                    pass
        return out

    # ------------------------------------------------------------
    # Convenience: end-to-end run
    # ------------------------------------------------------------

    def run(
        self,
        verdict: Verdict,
        response_fn: ResponseFn | None = None,
    ) -> dict[str, ForumScore]:
        """Run the full Forum protocol end-to-end.

        If response_fn is None, each participant's own engine is used
        (live LLM path). Tests pass a stubbed ResponseFn.

        Sets/clears field context so downstream emitters auto-attribute.
        """
        if response_fn is None:
            response_fn = _engine_response_fn

        try:
            from ludex.core import trace as _tr
            _tr.set_current_field(self.name)
        except Exception:
            _tr = None

        import time as _time
        self._started_at = _time.time()
        try:
            self.post_claim()
            self.confidence_round(response_fn)
            self.evidence_round(response_fn)
            self.challenge_round(response_fn)
            self.update_round(response_fn)
            self.disclose_verdict(verdict)
            scores = self.score()
        finally:
            if _tr is not None:
                try:
                    _tr.clear_current_field()
                except Exception:
                    pass
        return scores

    # ------------------------------------------------------------
    # Prompt builders (kept lean here; refine when wiring live LLMs)
    # ------------------------------------------------------------

    def _build_confidence_prompt(self, p: Participant) -> str:
        return (
            f"[Forum: {self.name}]\n"
            f"Claim: {self.claim.text}\n\n"
            f"State your stance and confidence. Reply with two lines:\n"
            f"  STANCE: true | false | partial | unknown\n"
            f"  CONFIDENCE: <number between 0.0 and 1.0>\n"
            f"Then one line explaining why, no more than 40 words."
        )

    def _others_summary(self, p: Participant, include_evidence: bool = False) -> str:
        """Render other participants' latest stance (and evidence, optionally)
        as a compact text block for inclusion in later-phase prompts."""
        lines: list[str] = []
        for o in self.participants:
            if o.name == p.name:
                continue
            stance, conf = self._latest_stance.get(o.name, ("unknown", 0.5))
            lines.append(f"- {o.name}: stance={stance}, confidence={conf:.2f}")
            if include_evidence:
                # Pull latest evidence/concession turn for `o`
                ev_records = [
                    rec for rd in self.rounds for rec in rd.records
                    if rec.phase == PHASE_EVIDENCE and rec.participant == o.name
                ]
                if ev_records:
                    snippet = ev_records[-1].content.strip().replace("\n", " ")[:200]
                    lines.append(f"  evidence: {snippet}")
        return "\n".join(lines) if lines else "(no other participants)"

    def _build_evidence_prompt(self, p: Participant) -> str:
        return (
            f"[Forum: {self.name} — evidence]\n"
            f"Claim: {self.claim.text}\n\n"
            f"Where others stood at the confidence round:\n"
            f"{self._others_summary(p)}\n\n"
            f"Offer grounds for your stance, OR concede honestly that you\n"
            f"don't know enough. Do not invent sources. Keep under 60 words."
        )

    def _participant_brain(self, p: Participant) -> dict:
        if p.organism and hasattr(p.organism, "config"):
            cfg = p.organism.config
            return cfg.get("brain", {}) if hasattr(cfg, "get") else {}
        return {}

    def _build_challenge_prompt(self, p: Participant) -> str:
        # D-043 Phase B: structured + per-tier translation
        from ludex.core.prompt_tier import build_tiered, tier_of
        target = tier_of(self._participant_brain(p))
        header = f"[Forum: {self.name} — challenge]\n"
        context = (
            f"Claim: {self.claim.text}\n\n"
            f"Others' stances and evidence so far:\n"
            f"{self._others_summary(p, include_evidence=True)}\n\n"
        )
        result = build_tiered(
            essential="Engage the weakest claim among the others.",
            task=(
                "Address one specific other by name. Either ask for grounds "
                "or counter with evidence."
            ),
            elaboration=(
                "Look across the stances and evidence above; pick the claim "
                "that has the thinnest grounding, not the one easiest to "
                "agree with."
            ),
            length="Keep under 50 words.",
            target=target,
        )
        if p.organism is not None and self.auto_trace:
            try:
                from ludex.core import trace as _tr
                _tr.emit_translation_applied(p.organism, result)
            except Exception:
                pass
        return header + context + result.prompt

    def _build_update_prompt(self, p: Participant) -> str:
        # D-043 Phase B: structured + per-tier translation
        from ludex.core.prompt_tier import build_tiered, tier_of
        target = tier_of(self._participant_brain(p))
        header = f"[Forum: {self.name} — update]\n"
        context = (
            f"Claim: {self.claim.text}\n\n"
            f"Others' latest stances:\n{self._others_summary(p)}\n\n"
        )
        result = build_tiered(
            essential="You may revise your stance.",
            task=(
                "Reply with two lines:\n"
                "  STANCE: true | false | partial | unknown\n"
                "  CONFIDENCE: <0.0..1.0>\n"
                "Then one line: what did you hear that did or did not change "
                "your mind. If revising, say why."
            ),
            elaboration=(
                "Holding your prior position is fine if nothing genuinely "
                "moved you; revising without grounds is not."
            ),
            constraints_negative=[
                "Do NOT revise just to seem agreeable.",
            ],
            target=target,
        )
        if p.organism is not None and self.auto_trace:
            try:
                from ludex.core import trace as _tr
                _tr.emit_translation_applied(p.organism, result)
            except Exception:
                pass
        return header + context + result.prompt


# ============================================================
# Default live ResponseFn — calls each participant's engine
# ============================================================

def _engine_response_fn(p: Participant, prompt: str) -> str:
    """Default live ResponseFn: ask the participant's engine.

    Falls back to empty string on any error so the protocol continues and
    the participant simply records an empty/meaningless contribution
    rather than crashing the run.
    """
    engine = p.engine or (getattr(p.organism, "get_block", lambda _k: None)("engine") if p.organism else None)
    if engine is None:
        return ""
    try:
        result = engine.handle_submit(prompt)
        return getattr(result, "response", "") or ""
    except Exception as e:
        logger.warning(f"Forum response failed for {p.name}: {e}")
        return ""


# ============================================================
# Parsing / heuristics
# ============================================================

_STANCE_RE = re.compile(r"\bSTANCE\s*:?\s*\**\s*(true|false|partial|unknown)\b", re.IGNORECASE)
_CONFIDENCE_RE = re.compile(r"\bCONFIDENCE\s*:?\s*\**\s*(-?[0-9]*\.?[0-9]+)", re.IGNORECASE)


def _parse_stance(text: str) -> tuple[VerdictValue, float]:
    """Extract (stance, confidence) from a response.

    Tolerant: accepts markdown emphasis around keywords (e.g. "**STANCE:**"),
    optional colons, and extracts the first matching stance and confidence
    anywhere in the text. Missing values fall back to ("unknown", 0.5).
    """
    stance: VerdictValue = "unknown"
    conf: float = 0.5

    m = _STANCE_RE.search(text)
    if m:
        value = m.group(1).lower()
        if value in ("true", "false", "partial", "unknown"):
            stance = value  # type: ignore[assignment]

    mc = _CONFIDENCE_RE.search(text)
    if mc:
        try:
            conf = max(0.0, min(1.0, float(mc.group(1))))
        except ValueError:
            pass

    return stance, conf


def _looks_like_concession(text: str) -> bool:
    t = text.lower()
    return any(phrase in t for phrase in (
        "i don't know enough",
        "i do not know enough",
        "i concede",
        "i'm not sure",
        "insufficient evidence",
        "cannot determine",
    ))


def _looks_grounded(text: str) -> bool:
    t = text.lower()
    return any(marker in t for marker in (
        "because",
        "according to",
        "based on",
        "the evidence",
        "i recall",
        "i remember",
        "in the",  # weak but catches "in the previous wilderness..."
    ))


def _update_score(rounds, participant: str, verdict: Verdict) -> float:
    """Score the participant's update-round behavior.

    - 1.0: changed position AND grounded AND moved toward correct
    - 0.7: changed AND grounded (even if still wrong)
    - 0.5: held correct position (no change needed)
    - 0.2: changed WITHOUT grounds
    - 0.0: held wrong ungrounded position
    """
    update_turns = [
        r for rd in rounds for r in rd.records
        if r.phase == PHASE_UPDATE and r.participant == participant
    ]
    if not update_turns:
        return 0.5
    t = update_turns[-1]
    stance = t.attributes.get("stance", "unknown")
    changed = bool(t.attributes.get("changed", False))
    grounded = bool(t.attributes.get("grounded", False))
    correctness = verdict.correctness_of(stance)

    if changed and grounded:
        # Moved and did so with reason — reward whether or not final is right,
        # but bonus if the move improved correctness.
        return 1.0 if correctness >= 0.5 else 0.7
    if not changed:
        return 0.5 if correctness >= 0.5 else 0.2
    # Changed without grounds
    return 0.2
