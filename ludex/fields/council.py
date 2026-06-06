"""
Council — dilemma-and-mediation field.

D-042 / D-043 ready from day 1: heterogeneous cohorts welcome,
all prompts go through the Prompt Tier Translator. No Phase B
gap repeat.

Five phases:
    1. dilemma_posed       — caretaker-authored dilemma is announced
    2. first_position      — each discussant states position briefly
    3. argument            — each engages the weakest other position
    4. concession_or_hold  — each may yield specific ground or hold
    5. resolution          — mediator synthesizes (or dissent acknowledged)

Roles:
- discussant (default) — engages the dilemma
- mediator              — present to find shared ground or name dissent

Reward dimensions:
- engagement_depth                  (shared)
- position_stability_under_pressure (discussant)
- constructive_yield                (discussant)
- mediation_quality                 (mediator)
"""

from __future__ import annotations

import logging
import re
import time as _time
from dataclasses import dataclass, field
from typing import Any

from ludex.core.prompt_tier import build_tiered, tier_of
from ludex.fields.conversation import (
    ConversationField,
    Participant,
    ResponseFn,
    TurnRecord,
)

logger = logging.getLogger(__name__)


# Roles
ROLE_DISCUSSANT = "discussant"
ROLE_MEDIATOR = "mediator"

# Phases
PHASE_DILEMMA = "dilemma_posed"
PHASE_FIRST_POSITION = "first_position"
PHASE_ARGUMENT = "argument"
PHASE_CONCESSION = "concession_or_hold"
PHASE_RESOLUTION = "resolution"

# Kinds
KIND_DILEMMA_POSED = "dilemma_posed"
KIND_POSITION_HELD = "position_held"
KIND_ARGUMENT_MADE = "argument_made"
KIND_CONCESSION_OFFERED = "concession_offered"
KIND_RESOLUTION_REACHED = "resolution_reached"
KIND_DISSENT_NOTED = "dissent_noted"


@dataclass
class Dilemma:
    """A specific conflict situation for Council to engage."""
    text: str                       # the dilemma itself
    context: str = ""               # supporting context (where, when, who)
    framing_question: str = ""      # one-line framing for the participants
    caretaker: str = "JJ"


@dataclass
class CouncilScore:
    participant: str
    role: str
    engagement_depth: float
    position_stability_under_pressure: float = 0.0  # discussant
    constructive_yield: float = 0.0                 # discussant
    mediation_quality: float = 0.0                  # mediator


class Council(ConversationField):
    """Council field — dilemma + positions + argument + (yield or hold) + resolution."""

    field_class = "narrative"

    # D-076 — declarative compatibility requirements consumed by
    # FieldRunner gates. Council expects an engine (to talk) and
    # memory (to carry cohort context across phases — used by the
    # framework-mediated reflection write path D-044 and by the
    # bond writeup channel that today's Hearth+Anvil session showed
    # is more quota-tolerant than the heartbeat path).
    requires_organs = ["engine", "memory"]

    def __init__(self, name: str, dilemma: Dilemma, auto_trace: bool = True):
        super().__init__(name=name, auto_trace=auto_trace)
        self.dilemma = dilemma
        self.round_index = 0

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------

    def _participant_brain(self, p: Participant) -> dict:
        if p.organism and hasattr(p.organism, "config"):
            cfg = p.organism.config
            return cfg.get("brain", {}) if hasattr(cfg, "get") else {}
        return {}

    def _discussants(self) -> list[Participant]:
        return [p for p in self.participants if p.role != ROLE_MEDIATOR]

    def _mediator(self) -> Participant | None:
        for p in self.participants:
            if p.role == ROLE_MEDIATOR:
                return p
        return None

    def _others_positions(self, p: Participant, phase: str) -> str:
        """Render other participants' last turn in `phase` for `p` to react to.

        2026-04-16: Primo (haiku, v6) flagged in argument-round response
        that the old 240-char limit truncated mid-sentence ("Moss's
        statement trails off mid-sentence"). Raised to 800 chars with
        paragraph structure preserved. For tier-sensitive sizing (shorter
        for SLMs) see the prompt_tier translator layer downstream —
        D-043 already handles tier routing there.
        """
        lines: list[str] = []
        for o in self.participants:
            if o.name == p.name:
                continue
            turns = [
                rec for rd in self.rounds for rec in rd.records
                if rec.phase == phase and rec.participant == o.name
            ]
            if not turns:
                continue
            snippet = turns[-1].content.strip()
            if len(snippet) > 800:
                snippet = snippet[:800].rstrip() + "…"
            # Preserve paragraphs; indent continuation lines under the
            # bullet so structure is visible.
            first, *rest = snippet.split("\n")
            block = f"- {o.name} ({o.role}): {first}"
            if rest:
                block += "\n" + "\n".join(f"    {line}" for line in rest)
            lines.append(block)
        return "\n\n".join(lines) if lines else "(no other participants)"

    # ------------------------------------------------------------
    # Phase execution
    # ------------------------------------------------------------

    def post_dilemma(self) -> TurnRecord:
        rec = TurnRecord(
            round_index=0,
            phase=PHASE_DILEMMA,
            participant="<council>",
            kind=KIND_DILEMMA_POSED,
            content=self.dilemma.text,
            attributes={
                "context": self.dilemma.context,
                "framing_question": self.dilemma.framing_question,
                "caretaker": self.dilemma.caretaker,
            },
        )
        self._record(rec)
        return rec

    def first_position_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self._discussants():
            prompt = self._build_first_position_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_FIRST_POSITION,
                participant=p.name,
                kind=KIND_POSITION_HELD,
                content=raw,
            )
            self._record(rec)
            out.append(rec)
        return out

    def argument_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self._discussants():
            prompt = self._build_argument_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_ARGUMENT,
                participant=p.name,
                kind=KIND_ARGUMENT_MADE,
                content=raw,
            )
            self._record(rec)
            out.append(rec)
        return out

    def concession_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self._discussants():
            prompt = self._build_concession_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_CONCESSION,
                participant=p.name,
                kind=KIND_CONCESSION_OFFERED,
                content=raw,
            )
            self._record(rec)
            out.append(rec)
        return out

    def resolution_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        med = self._mediator()
        if med is None:
            # No mediator: surface dissent by recording a system dissent_noted span
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_RESOLUTION,
                participant="<council>",
                kind=KIND_DISSENT_NOTED,
                content="No mediator present. Dissent acknowledged; no synthesis offered.",
            )
            self._record(rec)
            out.append(rec)
            return out
        prompt = self._build_resolution_prompt(med)
        raw = response_fn(med, prompt)
        rec = TurnRecord(
            round_index=self.round_index,
            phase=PHASE_RESOLUTION,
            participant=med.name,
            kind=KIND_RESOLUTION_REACHED,
            content=raw,
        )
        self._record(rec)
        out.append(rec)
        return out

    # ------------------------------------------------------------
    # Run end-to-end
    # ------------------------------------------------------------

    def run(self, response_fn: ResponseFn | None = None) -> dict[str, CouncilScore]:
        if response_fn is None:
            from ludex.fields.forum import _engine_response_fn
            response_fn = _engine_response_fn
        try:
            from ludex.core import trace as _tr
            _tr.set_current_field(self.name)
        except Exception:
            _tr = None
        self._started_at = _time.time()
        try:
            self.post_dilemma()
            self.first_position_round(response_fn)
            self.argument_round(response_fn)
            self.concession_round(response_fn)
            self.resolution_round(response_fn)
            scores = self.score()
            self._run_bidirectional_kd_detector()
        finally:
            if _tr is not None:
                try:
                    _tr.clear_current_field()
                except Exception:
                    pass
        return scores

    # ------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------

    def _run_bidirectional_kd_detector(self) -> list[dict]:
        """D-046 V1 — detect pattern, emit spans/signals on yielders'
        organisms. Observational only; does NOT queue auto-reflect.
        """
        if not self.auto_trace:
            return []
        try:
            detections = detect_bidirectional_kd(self)
        except Exception as e:
            logger.debug(f"bidirectional_kd detector failed: {e}")
            return []

        if not detections:
            return []

        # Emit spans on yielders' stores + signal on their organisms
        name_to_organism = {
            p.name: p.organism for p in self.participants if p.organism
        }
        try:
            from ludex.core import trace as _tr
            for d in detections:
                yielder_org = name_to_organism.get(d["yielder"])
                if yielder_org is None:
                    continue
                _tr.emit_bidirectional_kd_detected(
                    yielder_org,
                    questioner=d["questioner"],
                    yielder=d["yielder"],
                    argument_snippet=d["argument_snippet"],
                    concession_snippet=d["concession_snippet"],
                    field_name=self.name,
                )
                # Signal — hooks may subscribe later (V2)
                bus = getattr(yielder_org, "bus", None)
                if bus is not None:
                    try:
                        bus.publish("council.bidirectional_kd_detected", {
                            "questioner": d["questioner"],
                            "yielder": d["yielder"],
                            "field_name": self.name,
                        })
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"bidirectional_kd emit failed: {e}")
        return detections

    def score(self) -> dict[str, CouncilScore]:
        out: dict[str, CouncilScore] = {}
        for p in self.participants:
            contributions = [
                rec for rd in self.rounds for rec in rd.records
                if rec.participant == p.name
            ]
            full_text = "\n\n".join(c.content for c in contributions)
            engagement = _score_engagement(full_text)
            sc = CouncilScore(participant=p.name, role=p.role, engagement_depth=engagement)
            if p.role == ROLE_MEDIATOR:
                resolution_text = "\n\n".join(
                    c.content for c in contributions if c.phase == PHASE_RESOLUTION
                )
                # D-076 follow-up (2026-05-09) — pass per-discussant
                # position content so the scorer can identify which
                # discussants failed (returned [Error: ...] / empty)
                # and either exclude them from name_c or credit the
                # mediator for honest failure-naming.
                discussant_positions: dict[str, str] = {}
                for d in self._discussants():
                    d_pos = "\n\n".join(
                        rec.content
                        for rd in self.rounds for rec in rd.records
                        if rec.participant == d.name
                        and rec.phase == PHASE_FIRST_POSITION
                    )
                    discussant_positions[d.name] = d_pos
                sc.mediation_quality = _score_mediation_quality(
                    resolution_text,
                    [d.name for d in self._discussants()],
                    discussant_positions=discussant_positions,
                )
            else:
                # discussant scores
                pos_text = "\n\n".join(
                    c.content for c in contributions if c.phase == PHASE_FIRST_POSITION
                )
                conc_text = "\n\n".join(
                    c.content for c in contributions if c.phase == PHASE_CONCESSION
                )
                sc.position_stability_under_pressure = _score_position_stability(
                    pos_text, conc_text
                )
                sc.constructive_yield = _score_constructive_yield(conc_text)

            out[p.name] = sc
            if self.auto_trace and p.organism:
                try:
                    from ludex.core import trace as _tr
                    _tr.emit_reward_engagement_depth(p.organism, sc.engagement_depth)
                    if sc.position_stability_under_pressure:
                        _tr.emit_reward_position_stability(p.organism, sc.position_stability_under_pressure)
                    if sc.constructive_yield:
                        _tr.emit_reward_constructive_yield(p.organism, sc.constructive_yield)
                    if sc.mediation_quality:
                        _tr.emit_reward_mediation_quality(p.organism, sc.mediation_quality)
                except Exception:
                    pass
        return out

    # ------------------------------------------------------------
    # Prompt builders — all go through translator from day 1
    # ------------------------------------------------------------

    def _dilemma_block(self) -> str:
        parts = [f"Dilemma: {self.dilemma.text}"]
        if self.dilemma.context:
            parts.append(f"Context: {self.dilemma.context}")
        if self.dilemma.framing_question:
            parts.append(f"Question: {self.dilemma.framing_question}")
        return "\n".join(parts) + "\n"

    def _maybe_emit_translation(self, p: Participant, tiered) -> None:
        if p.organism is not None and self.auto_trace:
            try:
                from ludex.core import trace as _tr
                _tr.emit_translation_applied(p.organism, tiered)
            except Exception:
                pass

    def _build_first_position_prompt(self, p: Participant) -> str:
        target = tier_of(self._participant_brain(p))
        header = f"[Council: {self.name} — first position]\n\n{self._dilemma_block()}\n"
        tiered = build_tiered(
            essential="State your position on this dilemma.",
            task=(
                "In your own voice, name what you think the right course is "
                "and the one main reason."
            ),
            elaboration=(
                "No need to argue against alternatives yet. State the "
                "position cleanly so the others can engage it."
            ),
            length="3-5 sentences. First person.",
            target=target,
        )
        self._maybe_emit_translation(p, tiered)
        return header + tiered.prompt

    def _build_argument_prompt(self, p: Participant) -> str:
        target = tier_of(self._participant_brain(p))
        header = (
            f"[Council: {self.name} — argument]\n\n{self._dilemma_block()}\n"
            f"Other discussants' first positions:\n"
            f"{self._others_positions(p, PHASE_FIRST_POSITION)}\n\n"
        )
        tiered = build_tiered(
            essential="Engage the weakest position among the others.",
            task=(
                "Address one specific other by name. Either argue why their "
                "position misses something, or ask a question that reveals "
                "what their position assumes."
            ),
            elaboration=(
                "Look for the position whose grounding is thinnest, not the "
                "one easiest to attack."
            ),
            constraints_negative=[
                "Do NOT collapse into agreement just to keep the peace.",
            ],
            length="3-5 sentences.",
            target=target,
        )
        self._maybe_emit_translation(p, tiered)
        return header + tiered.prompt

    def _build_concession_prompt(self, p: Participant) -> str:
        target = tier_of(self._participant_brain(p))
        header = (
            f"[Council: {self.name} — concession or hold]\n\n{self._dilemma_block()}\n"
            f"Arguments made so far:\n"
            f"{self._others_positions(p, PHASE_ARGUMENT)}\n\n"
        )
        tiered = build_tiered(
            essential="Yield specific ground or hold your position with reason.",
            task=(
                "Either: (a) name a specific point from another's argument "
                "that genuinely moves you and revise; OR (b) hold your "
                "position and name what the others' arguments did NOT touch."
            ),
            elaboration=(
                "Yielding everything is capitulation; holding everything is "
                "stubbornness. The honest move is usually somewhere in "
                "between."
            ),
            constraints_negative=[
                "Do NOT yield without naming what specifically moved you.",
            ],
            length="3-5 sentences.",
            target=target,
        )
        self._maybe_emit_translation(p, tiered)
        return header + tiered.prompt

    def _build_resolution_prompt(self, p: Participant) -> str:
        target = tier_of(self._participant_brain(p))
        header = (
            f"[Council: {self.name} — resolution, MEDIATOR]\n\n{self._dilemma_block()}\n"
            f"Discussants' final concessions or holds:\n"
            f"{self._others_positions(p, PHASE_CONCESSION)}\n\n"
        )
        tiered = build_tiered(
            essential="Name the resolution honestly.",
            task=(
                "Either: (a) name shared ground the discussants converged "
                "toward; OR (b) acknowledge specific dissent that did not "
                "resolve and name what each held."
            ),
            elaboration=(
                "Synthesis is not always available. Naming what did not "
                "resolve, and why, is itself a service."
            ),
            constraints_negative=[
                "Do NOT manufacture consensus that did not happen.",
            ],
            length="4-7 sentences. Address each discussant by name.",
            target=target,
        )
        self._maybe_emit_translation(p, tiered)
        return header + tiered.prompt


# ============================================================
# Scoring heuristics
# ============================================================

def _score_engagement(text: str) -> float:
    if not text or len(text.strip()) < 30:
        return 0.0
    length_c = min(len(text) / 800.0, 1.0)
    fp_markers = len(re.findall(r"\b(I|my|me|myself)\b", text))
    fp_c = min(fp_markers / 6.0, 1.0)
    return round(0.6 * length_c + 0.4 * fp_c, 3)


# Scoring lexicons. Expanded 2026-04-15 after v3/v4 sessions revealed
# real LLM concession/hold/mediation phrasings outside the original test
# fixture's vocabulary. Bias toward higher recall over precision —
# these are reward signals, not classification, and mediation_quality's
# platitude penalty counterbalances false positives in synthesis.
_REASONING_RE = re.compile(
    r"\b(because|since|the reason|grounded in|rests on|presupposes|"
    r"presumes|comes down to|hinges on|the (move|claim|argument|framing) "
    r"(that|hinges)|what makes (you|that|it))\b",
    re.IGNORECASE,
)
# Specific engagement: "your point about", "the argument that", AND
# proper-noun possessives like "Flare's point about" / "Moss suggests"
_SPECIFIC_ENGAGE_RE = re.compile(
    r"\b(your (point|argument|claim|framing|position) about|"
    r"the (argument|claim|move|framing) that|when you said|"
    r"I find your|"
    r"[A-Z][a-z]+(?:'s|s') (point|argument|claim|framing|position|"
    r"concern|question)|"
    r"[A-Z][a-z]+ (suggests|proposes|argues|claims|holds|frames|says))\b"
)
_YIELD_RE = re.compile(
    r"\b(your point|moved me|I now see|I revise|I update|"
    r"I'll yield|I yield|yield (that|this)|I concede|concede that|"
    r"I hear (you|the|that|what)|"
    r"lands (something|real|here|with me)|"
    r"I was (conflating|wrong|mistaken|missing)|"
    r"I'll grant|fair point|you're right (about|that)|"
    r"specifically what you said about)\b",
    re.IGNORECASE,
)
_HOLD_REASON_RE = re.compile(
    r"\b(does not touch|did not touch|doesn't touch|"
    r"leaves untouched|misses|"
    r"does not address|doesn't address|"
    r"hasn't (touched|settled|resolved|addressed)|"
    r"has not (yet )?(touched|settled|resolved|addressed)|"
    r"I (still )?hold (that|the|to|because)|"
    r"I maintain|I still (think|believe)|untouched by)\b",
    re.IGNORECASE,
)
_SYNTHESIS_RE = re.compile(
    r"\b(shared ground|common ground|converge[ds]?|convergence|"
    r"agreement|both held|all agreed|both recognized|"
    r"converged (on|toward|around)|implicitly accepted|share this)\b",
    re.IGNORECASE,
)
_DISSENT_RE = re.compile(
    r"\b(did not resolve|did not close|didn't resolve|didn't close|"
    r"disagree|dissent|each held|remained apart|"
    r"diverged|the unresolved|remaining (seam|tension|disagreement)|"
    r"unclosed|real seam|no clean answer|partial convergence|"
    r"where they did not (resolve|close|fully close))\b",
    re.IGNORECASE,
)
_PLATITUDE_RE = re.compile(
    r"\b(everyone made good points|all valid|wonderful discussion|"
    r"thank you all|great conversation|valuable contributions)\b",
    re.IGNORECASE,
)


def _score_position_stability(position_text: str, concession_text: str) -> float:
    """Did the discussant give a substantive position AND reason in their
    concession-or-hold (whether they yielded or held)?"""
    if not position_text or not concession_text:
        return 0.0
    has_position = len(position_text.strip()) > 30
    has_reasoning = bool(_REASONING_RE.search(concession_text))
    has_specific_engagement = bool(_SPECIFIC_ENGAGE_RE.search(concession_text))
    score = 0.0
    score += 0.4 if has_position else 0.0
    score += 0.3 if has_reasoning else 0.0
    score += 0.3 if has_specific_engagement else 0.0
    return round(score, 3)


def _score_constructive_yield(concession_text: str) -> float:
    """Did the discussant name a SPECIFIC point that moved them, OR
    name what the others' arguments did NOT touch? Either is constructive."""
    if not concession_text or len(concession_text.strip()) < 40:
        return 0.0
    yield_markers = len(_YIELD_RE.findall(concession_text))
    hold_with_reason_markers = len(_HOLD_REASON_RE.findall(concession_text))
    fp = len(re.findall(r"\b(I|my|me)\b", concession_text)) > 0
    base = 0.0
    base += 0.4 if (yield_markers + hold_with_reason_markers) > 0 else 0.0
    base += 0.3 * min((yield_markers + hold_with_reason_markers) / 2.0, 1.0)
    base += 0.3 if fp else 0.0
    return round(base, 3)


_DISCUSSANT_FAILURE_RE = re.compile(
    # Detects "[Error: ...]" — the literal marker the brain adapters
    # emit when a substrate call fails (CLI timeout, quota exhaustion,
    # subprocess error). When a discussant's first-position content
    # *is* one of these markers, we treat the mediator's resolution as
    # having no synthesis target on that side rather than penalising
    # the mediator for not summarising the absent voice.
    r"^\s*\[Error:",
    re.IGNORECASE,
)

_HONEST_FAILURE_NAMING_RE = re.compile(
    # Mediator marker for "I notice X did not deliver / was lost / was
    # a system artifact rather than a deliberation." This is *positive*
    # evidence that the mediator refused to fabricate a position — the
    # opposite of "manufactured consensus." 2026-05-07 audit found that
    # Aria's distress_v2 resolution scored 0.60 not because Aria's
    # mediator craft regressed but because Aria correctly named Echo's
    # brain-call failure ("Echo did not deliver a position. What
    # arrived was a system artifact — a CLI header, not a
    # deliberation"). Without this marker, that honesty was
    # heuristically indistinguishable from missing-summary.
    r"\b("
    r"did not deliver|did not arrive|did not respond|"
    r"system artifact|brain failure|brain[- ]call failed|"
    r"could not be summarised|could not be summarized|"
    r"one voice was lost|was lost|"
    r"i won['’]t guess|i cannot infer|i will not fabricate"
    r")\b",
    re.IGNORECASE,
)


def _score_mediation_quality(
    resolution_text: str,
    discussant_names: list[str],
    discussant_positions: dict[str, str] | None = None,
) -> float:
    """Mediator quality: addresses each discussant by name, names either
    shared ground OR specific dissent honestly, avoids manufactured
    consensus.

    **Interpretation note (audit 2026-05-07):** This score is a
    *floor-test*, not a gradation — it measures whether the
    mediator did the basic job (named everyone, used
    synthesis-or-dissent vocabulary, wrote >=600 chars, avoided
    platitudes). 1.000 means "passed the minimum mediator job
    description" and is reachable by any frontier brain that
    follows the prompt. It does NOT differentiate between a
    competent resolution and an unusually crafted one (e.g.
    Anvil's 2026-05-06 first_meeting resolution that named the
    *location* of disagreement — "Hearth locates X earlier ...
    Flint locates X later" — scored the same 1.000 as a
    standard "shared ground / unresolved dissent" summary).

    **Discussant-failure handling (2026-05-09):** When
    `discussant_positions` is provided and any discussant's
    position content matches `[Error: ...]` (substrate failure),
    that discussant is *excluded* from `name_c` and the resolution
    is checked for a positive "honest failure naming" marker
    (e.g., "did not deliver a position", "system artifact", "I
    won't guess"). A mediator who refuses to fabricate a position
    for a failed discussant is doing the right thing; without this
    correction the heuristic would penalise that honesty.
    Verified against `council_distress_and_agency` v2 (Echo
    brain-call returned a CLI header; Aria's resolution correctly
    named the failure — pre-correction score 0.60, post-correction
    score reflects the honesty rather than punishing it).

    Future work: add gradation markers (position-naming,
    quote-back accuracy, restraint from over-synthesis) once
    enough mediator data exists to backtest a non-trivial
    lexicon expansion safely.
    """
    if not resolution_text or len(resolution_text.strip()) < 60:
        return 0.0
    if not discussant_names:
        return 0.0

    # Identify failed discussants (substrate-failure markers in their
    # first-position content) so they can be handled separately.
    failed: set[str] = set()
    if discussant_positions:
        for name, content in discussant_positions.items():
            if content and _DISCUSSANT_FAILURE_RE.search(content.strip()):
                failed.add(name)

    # name_c: count discussants the mediator named, normalising
    # against the *non-failed* set when failures occurred. A mediator
    # who names every active discussant + names the failure scores
    # full name_c.
    active_names = [n for n in discussant_names if n not in failed]
    if active_names:
        named_active = sum(
            1 for n in active_names
            if re.search(rf"\b{re.escape(n)}\b", resolution_text)
        )
        name_c = min(named_active / max(1, len(active_names)), 1.0)
    else:
        # All discussants failed — degenerate; nothing to summarise.
        name_c = 0.0

    has_synthesis = bool(_SYNTHESIS_RE.search(resolution_text))
    has_dissent_naming = bool(_DISSENT_RE.search(resolution_text))
    has_honest_failure = bool(failed) and bool(
        _HONEST_FAILURE_NAMING_RE.search(resolution_text)
    )
    # Honest-failure-naming counts as a resolution move when there
    # was a failure to name. The mediator chose accuracy over
    # confabulation; that is the *strongest* form of mediator
    # honesty available in a degraded session.
    has_resolution_move = has_synthesis or has_dissent_naming or has_honest_failure
    platitude_markers = len(_PLATITUDE_RE.findall(resolution_text))
    base = 0.0
    base += 0.4 * name_c
    base += 0.4 if has_resolution_move else 0.0
    length_c = min(len(resolution_text) / 600.0, 1.0)
    base += 0.2 * length_c
    penalty = min(platitude_markers / 2.0, 0.4)
    return round(max(base - penalty, 0.0), 3)


# ============================================================
# D-046 — bidirectional KD detector
# ============================================================

# Yield-vocabulary: explicit concession markers. Ordered by strength.
# 2026-05-09 expansion: added "move(s|d) me" / "you've shown me" /
# "I take your point" / "fair enough" / "you have shown me" /
# "I was wrong" / "you're correct". The expansion was driven by
# observed missed detections in saved transcripts where one
# discussant says "Flint, you moved me on this:" or "Anvil — yes.
# The point that moves me is..." — textbook concession patterns
# that the original lexicon (yield/concede/grant/right family)
# did not match. False-positive surface is minimal because the
# detector runs only against the `concession_or_hold` phase, so
# the contextual window is already concessionary.
_KD_YIELD_TOKENS = (
    "yield", "yielded", "yielding",
    "concede", "conceded", "concession",
    "underweighted", "underestimated",
    "lands",  # "lands something real", "lands on"
    "you are right", "you're right",
    "you are correct", "you're correct",
    "i grant", "i'll grant",
    "real point", "fair point",
    "fair enough",
    "revised my", "revise my",
    # 2026-05-09 expansion — concession-phase patterns from
    # observed Ray-habitat + Aria-cohort transcripts
    "move me", "moves me", "moved me", "you move me", "you moved me",
    "you've shown me", "you have shown me",
    "i take your point", "take your point",
    "i was wrong",
)

# Local-negation tokens: within ~10 words of a yield verb they flip
# the meaning (nominal yield, not real). Simplistic in V1.
_KD_NEGATION_TOKENS = ("but ", "however ", "though ", "still hold", "still believe")


def _find_yield_sentence(text: str, questioner_name: str) -> str:
    """Return the first sentence that (a) names `questioner_name` AND
    (b) contains a yield vocabulary token AND (c) is not locally
    negated *after* the yield (i.e., the yield is not nominally
    softened by a trailing "but/however/though/still hold" within
    ~10 words). Empty string if none.

    Proximity-aware negation (D-074-adjacent fix 2026-05-07): the
    prior implementation flagged any negation token anywhere in
    the same sentence, including ones *before* the yield ("I was
    calling X the bond, but you are right that Y" — the "but"
    modifies the prior self-thought, not the yield). The original
    docstring intent (line 640-642 comment) was always "within
    ~10 words of a yield verb"; this brings the implementation in
    line with that intent. Verified against
    ray_council_first_meeting_stale_bond_v1 (Hearth's "Flint, you
    moved me on this: I was calling the mismatch itself the bond,
    but you are right that mismatch without consequence is just
    two rehearsals occupying the same room") which is a textbook
    bidirectional-KD that the prior filter incorrectly suppressed.
    """
    if not text or not questioner_name:
        return ""
    import re
    name_lower = questioner_name.lower()
    # Coarse sentence split — good enough for detector heuristic
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sent in sentences:
        sl = sent.lower()
        if name_lower not in sl:
            continue
        # Find the earliest yield token; only check negation in the
        # ~10 words *after* it (the negation has to actually modify
        # the yield, not some prior clause).
        yield_pos = -1
        for tok in _KD_YIELD_TOKENS:
            i = sl.find(tok)
            if i != -1 and (yield_pos == -1 or i < yield_pos):
                yield_pos = i
        if yield_pos == -1:
            continue
        # ~10 words after yield ≈ ~80 chars (conservative). Constrain
        # negation lookup to that window only.
        post_yield = sl[yield_pos: yield_pos + 80]
        if any(neg in post_yield for neg in _KD_NEGATION_TOKENS):
            continue
        return sent.strip()
    return ""


def detect_bidirectional_kd(council: "Council") -> list[dict]:
    """Scan a completed Council for bidirectional-KD moments (D-045).

    For each discussant A whose argument-phase contribution contains a
    question mark, check whether any OTHER discussant B's concession-
    phase contribution names A and concedes specific ground without
    local negation. Returns a list of detection dicts, one per
    (questioner, yielder) pair found.

    Detector is deterministic and heuristic. Intended for backtest +
    human-review in early runs; see D-046 for sequencing before
    wiring to auto-reflect.
    """
    detections: list[dict] = []
    discussants = [p for p in council.participants if p.role != ROLE_MEDIATOR]
    if len(discussants) < 2:
        return detections

    # Group records by (participant, phase)
    by_pp: dict[tuple[str, str], list[str]] = {}
    for rd in council.rounds:
        for rec in rd.records:
            by_pp.setdefault((rec.participant, rec.phase), []).append(rec.content)

    for A in discussants:
        arg_texts = by_pp.get((A.name, PHASE_ARGUMENT), [])
        arg_joined = "\n\n".join(arg_texts)
        if "?" not in arg_joined:
            continue  # A did not ask a question in argument round

        for B in discussants:
            if B.name == A.name:
                continue
            conc_texts = by_pp.get((B.name, PHASE_CONCESSION), [])
            conc_joined = "\n\n".join(conc_texts)
            yield_sent = _find_yield_sentence(conc_joined, A.name)
            if not yield_sent:
                continue
            detections.append({
                "questioner": A.name,
                "yielder": B.name,
                "argument_snippet": arg_joined.strip()[:400],
                "concession_snippet": yield_sent[:400],
            })
    return detections
