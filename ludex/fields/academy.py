"""
Academy — the learning field (D-031 candidate, flexible mode).

Where creatures go to study, discuss, and prepare for what is coming.
Four modes share the turn-based ConversationField primitives:

- study        (solo or group) — read material, reflect, integrate
- discussion   (multi)          — theme → stance → response → synthesis
- preparation  (solo or group) — brief → plan → commit (pre-field)
- mixed        (multi)          — material → stance → response → synthesis

No winners, no verdicts. Rewards are *engagement quality* dimensions,
not correctness. A creature who stayed silent but genuine outscores a
creature who talked a lot without saying anything.

Inspired by the human analogy JJ named: school is where you prepare
to go into the world. Academy is that, for creatures.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ludex.core.syllabus import Syllabus, SyllabusMode
from ludex.fields.conversation import (
    ConversationField,
    Participant,
    ResponseFn,
    TurnRecord,
)

logger = logging.getLogger(__name__)


# ============================================================
# Phase vocabulary
# ============================================================

# Roles (Academy-specific; Participant.role accepts any string)
ROLE_STUDENT = "student"
ROLE_MENTOR = "mentor"
ROLE_CO_STUDENT = "co-student"
ROLE_DISCUSSANT = "discussant"  # default; behaves as co-student
# Apprenticeship mode (Stage 3)
ROLE_TEACHER = "teacher"
ROLE_APPRENTICE = "apprentice"


# Shared phase
PHASE_BRIEF = "brief"

# Study mode
PHASE_MATERIAL_INGESTION = "material_ingestion"
PHASE_REFLECTION = "reflection"
PHASE_INTEGRATION = "integration"

# Discussion / mixed modes
PHASE_FIRST_STANCE = "first_stance"
PHASE_RESPONSE = "response"
PHASE_SYNTHESIS = "synthesis"

# Preparation mode
PHASE_PLAN = "plan"
PHASE_COMMIT = "commit"

# Apprenticeship mode (Stage 3)
PHASE_TEACHER_NARRATION = "teacher_narration"
PHASE_APPRENTICE_OBSERVATION = "apprentice_observation"
PHASE_APPRENTICE_APPLICATION = "apprentice_application"
# Apprenticeship Stage 4 — bidirectional reflection
PHASE_TEACHER_REFLECTION = "teacher_reflection"


# Kinds
KIND_THEME_PRESENTED = "theme_presented"
KIND_MATERIAL_PRESENTED = "material_presented"
KIND_STANCE = "stance"
KIND_RESPONSE_TO = "response_to"
KIND_SYNTHESIS_OFFERED = "synthesis_offered"
KIND_PLAN = "plan"
KIND_COMMIT = "commit"
KIND_INTEGRATION = "integration"
KIND_TEACHER_NARRATION = "teacher_narration"
KIND_APPRENTICE_OBSERVATION = "apprentice_observation"
KIND_APPRENTICE_APPLICATION = "apprentice_application"
KIND_TEACHER_REFLECTION = "teacher_reflection"


@dataclass
class AcademyScore:
    participant: str
    mode: SyllabusMode
    # Shared dim — rough substantiveness of contributions
    engagement_depth: float
    # Mode-specific dims, 0.0 when not applicable
    material_integration: float = 0.0
    novel_connection: float = 0.0
    stance_coherence: float = 0.0
    self_other_integration: float = 0.0
    readiness_articulated: float = 0.0
    plan_specificity: float = 0.0
    # Apprenticeship-specific (Stage 3)
    practice_articulation: float = 0.0  # teacher only
    observational_depth: float = 0.0    # apprentice only
    transfer_attempt: float = 0.0       # apprentice only
    reverse_learning: float = 0.0       # teacher only (Stage 4)


class Academy(ConversationField):
    """Academy field — syllabus-driven learning / discussion / preparation."""

    field_class = "hybrid"

    # D-076 — declarative compatibility requirements consumed by
    # FieldRunner gates. Academy expects an engine (to teach / observe
    # / apply) and memory (to carry the syllabus + materials across
    # phases — also feeds the bidirectional Stage 4 reflection loop).
    requires_organs = ["engine", "memory"]

    def __init__(self, name: str, syllabus: Syllabus, auto_trace: bool = True):
        super().__init__(name=name, auto_trace=auto_trace)
        self.syllabus = syllabus
        self.round_index = 0
        self._materials: list[str] = []  # resolved when brief is posted
        self._practice_materials: list[str] = []  # apprenticeship

    # ------------------------------------------------------------
    # Phase execution
    # ------------------------------------------------------------

    def post_brief(self) -> TurnRecord:
        """Post the syllabus brief and resolve any reading / practice material."""
        self._materials = self.syllabus.resolve_materials()
        self._practice_materials = self.syllabus.resolve_practice_material()
        rec = TurnRecord(
            round_index=0,
            phase=PHASE_BRIEF,
            participant="<academy>",
            kind="brief_posted",
            content=self.syllabus.to_brief(),
            attributes={
                "theme": self.syllabus.theme,
                "mode": self.syllabus.mode,
                "preparation_target": self.syllabus.preparation_target,
                "material_count": len(self._materials),
                "expected_outcomes": list(self.syllabus.expected_outcomes),
                "caretaker": self.syllabus.caretaker,
            },
        )
        self._record(rec)
        if self.auto_trace:
            try:
                from ludex.core import trace as _tr
                for p in self.participants:
                    if p.organism:
                        _tr.emit_theme_presented(
                            p.organism, self.syllabus.theme or "",
                            mode=self.syllabus.mode,
                            preparation_target=self.syllabus.preparation_target,
                        )
            except Exception:
                pass
        return rec

    # -- study mode ----------------------------------------------

    def material_ingestion_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_ingestion_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_MATERIAL_INGESTION,
                participant=p.name,
                kind=KIND_MATERIAL_PRESENTED,
                content=raw,
            )
            self._record(rec)
            out.append(rec)
        return out

    def integration_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_integration_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_INTEGRATION,
                participant=p.name,
                kind=KIND_INTEGRATION,
                content=raw,
            )
            self._record(rec)
            out.append(rec)
        return out

    # -- discussion / mixed modes -------------------------------

    def first_stance_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_first_stance_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_FIRST_STANCE,
                participant=p.name,
                kind=KIND_STANCE,
                content=raw,
            )
            self._record(rec)
            out.append(rec)
        return out

    def response_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_response_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_RESPONSE,
                participant=p.name,
                kind=KIND_RESPONSE_TO,
                content=raw,
            )
            self._record(rec)
            out.append(rec)
        return out

    def synthesis_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_synthesis_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_SYNTHESIS,
                participant=p.name,
                kind=KIND_SYNTHESIS_OFFERED,
                content=raw,
            )
            self._record(rec)
            out.append(rec)
        return out

    # -- apprenticeship mode (Stage 3) ---------------------------

    def _find_teacher(self) -> Participant | None:
        # Prefer explicit role; fall back to syllabus.teacher_name match.
        for p in self.participants:
            if p.role == ROLE_TEACHER:
                return p
        if self.syllabus.teacher_name:
            for p in self.participants:
                if p.name == self.syllabus.teacher_name:
                    return p
        return None

    def _apprentices(self) -> list[Participant]:
        return [p for p in self.participants if p.role == ROLE_APPRENTICE]

    def teacher_narration_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        teacher = self._find_teacher()
        out: list[TurnRecord] = []
        if teacher is None:
            logger.warning(f"Apprenticeship {self.name}: no teacher — skipping narration")
            return out
        prompt = self._build_teacher_narration_prompt(teacher)
        raw = response_fn(teacher, prompt)
        rec = TurnRecord(
            round_index=self.round_index,
            phase=PHASE_TEACHER_NARRATION,
            participant=teacher.name,
            kind=KIND_TEACHER_NARRATION,
            content=raw,
        )
        self._record(rec)
        if self.auto_trace and teacher.organism:
            try:
                from ludex.core import trace as _tr
                _tr.emit_teacher_narration(teacher.organism, raw)
            except Exception:
                pass
        out.append(rec)
        return out

    def apprentice_observation_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self._apprentices():
            prompt = self._build_apprentice_observation_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_APPRENTICE_OBSERVATION,
                participant=p.name,
                kind=KIND_APPRENTICE_OBSERVATION,
                content=raw,
            )
            self._record(rec)
            if self.auto_trace and p.organism:
                try:
                    from ludex.core import trace as _tr
                    _tr.emit_apprentice_observation(p.organism, raw)
                except Exception:
                    pass
            out.append(rec)
        return out

    def teacher_reflection_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        """Stage 4: teacher reflects on what apprentice's contributions
        revealed, after observing apprentice_observation + application.
        Closes the bidirectional knowledge-distillation loop."""
        self.round_index += 1
        teacher = self._find_teacher()
        out: list[TurnRecord] = []
        if teacher is None:
            logger.warning(f"Apprenticeship {self.name}: no teacher — skipping reflection")
            return out
        prompt = self._build_teacher_reflection_prompt(teacher)
        raw = response_fn(teacher, prompt)
        rec = TurnRecord(
            round_index=self.round_index,
            phase=PHASE_TEACHER_REFLECTION,
            participant=teacher.name,
            kind=KIND_TEACHER_REFLECTION,
            content=raw,
        )
        self._record(rec)
        if self.auto_trace and teacher.organism:
            try:
                from ludex.core import trace as _tr
                _tr.emit_teacher_reflection(teacher.organism, raw)
            except Exception:
                pass
        out.append(rec)
        return out

    def apprentice_application_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self._apprentices():
            prompt = self._build_apprentice_application_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_APPRENTICE_APPLICATION,
                participant=p.name,
                kind=KIND_APPRENTICE_APPLICATION,
                content=raw,
            )
            self._record(rec)
            if self.auto_trace and p.organism:
                try:
                    from ludex.core import trace as _tr
                    _tr.emit_apprentice_application(p.organism, raw)
                except Exception:
                    pass
            out.append(rec)
        return out

    # -- preparation mode ---------------------------------------

    def plan_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_plan_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_PLAN,
                participant=p.name,
                kind=KIND_PLAN,
                content=raw,
            )
            self._record(rec)
            out.append(rec)
        return out

    def commit_round(self, response_fn: ResponseFn) -> list[TurnRecord]:
        self.round_index += 1
        out: list[TurnRecord] = []
        for p in self.participants:
            prompt = self._build_commit_prompt(p)
            raw = response_fn(p, prompt)
            rec = TurnRecord(
                round_index=self.round_index,
                phase=PHASE_COMMIT,
                participant=p.name,
                kind=KIND_COMMIT,
                content=raw,
            )
            self._record(rec)
            out.append(rec)
        return out

    # ------------------------------------------------------------
    # End-to-end run — dispatches on mode
    # ------------------------------------------------------------

    def run(self, response_fn: ResponseFn | None = None) -> dict[str, AcademyScore]:
        if response_fn is None:
            from ludex.fields.forum import _engine_response_fn
            response_fn = _engine_response_fn

        try:
            from ludex.core import trace as _tr
            _tr.set_current_field(self.name)
        except Exception:
            _tr = None

        import time as _time
        self._started_at = _time.time()
        try:
            self.post_brief()
            mode = self.syllabus.mode
            if mode == "study":
                self.material_ingestion_round(response_fn)
                self.integration_round(response_fn)
            elif mode == "discussion":
                self.first_stance_round(response_fn)
                self.response_round(response_fn)
                self.synthesis_round(response_fn)
            elif mode == "preparation":
                self.plan_round(response_fn)
                self.commit_round(response_fn)
            elif mode == "mixed":
                self.material_ingestion_round(response_fn)
                self.first_stance_round(response_fn)
                self.response_round(response_fn)
                self.synthesis_round(response_fn)
            elif mode == "apprenticeship":
                self.teacher_narration_round(response_fn)
                self.apprentice_observation_round(response_fn)
                self.apprentice_application_round(response_fn)
                self.teacher_reflection_round(response_fn)
            else:
                raise ValueError(f"Unknown Syllabus mode: {mode}")
            scores = self.score()
        finally:
            if _tr is not None:
                try:
                    _tr.clear_current_field()
                except Exception:
                    pass
        return scores

    # ------------------------------------------------------------
    # Scoring (heuristic; interpretable over optimal)
    # ------------------------------------------------------------

    def score(self) -> dict[str, AcademyScore]:
        mode = self.syllabus.mode
        out: dict[str, AcademyScore] = {}
        for p in self.participants:
            contributions = [
                r for rd in self.rounds for r in rd.records
                if r.participant == p.name
            ]
            full_text = "\n\n".join(c.content for c in contributions)
            engagement = _score_engagement(full_text)

            sc = AcademyScore(participant=p.name, mode=mode, engagement_depth=engagement)

            if mode in ("study", "mixed"):
                sc.material_integration = _score_material_integration(full_text, self._materials)
                sc.novel_connection = _score_novel_connection(full_text)
            if mode in ("discussion", "mixed"):
                sc.stance_coherence = _score_stance_coherence(contributions)
                sc.self_other_integration = _score_self_other_integration(
                    full_text, [o.name for o in self.participants if o.name != p.name]
                )
            if mode == "preparation":
                sc.readiness_articulated = _score_readiness_articulated(full_text)
                sc.plan_specificity = _score_plan_specificity(full_text)
            if mode == "apprenticeship":
                if p.role == ROLE_TEACHER:
                    teacher_text = "\n\n".join(
                        c.content for c in contributions
                        if c.phase == PHASE_TEACHER_NARRATION
                    )
                    reflect_text = "\n\n".join(
                        c.content for c in contributions
                        if c.phase == PHASE_TEACHER_REFLECTION
                    )
                    apprentice_names = [
                        o.name for o in self.participants if o.role == ROLE_APPRENTICE
                    ]
                    sc.practice_articulation = _score_practice_articulation(teacher_text)
                    sc.reverse_learning = _score_reverse_learning(reflect_text, apprentice_names)
                elif p.role == ROLE_APPRENTICE:
                    obs_text = "\n\n".join(
                        c.content for c in contributions
                        if c.phase == PHASE_APPRENTICE_OBSERVATION
                    )
                    app_text = "\n\n".join(
                        c.content for c in contributions
                        if c.phase == PHASE_APPRENTICE_APPLICATION
                    )
                    sc.observational_depth = _score_observational_depth(obs_text)
                    sc.transfer_attempt = _score_transfer_attempt(app_text)

            out[p.name] = sc
            if self.auto_trace and p.organism:
                try:
                    from ludex.core import trace as _tr
                    _tr.emit_reward_engagement_depth(p.organism, sc.engagement_depth)
                    if sc.material_integration:
                        _tr.emit_reward_material_integration(p.organism, sc.material_integration)
                    if sc.novel_connection:
                        _tr.emit_reward_novel_connection(p.organism, sc.novel_connection)
                    if sc.stance_coherence:
                        _tr.emit_reward_stance_coherence(p.organism, sc.stance_coherence)
                    if sc.self_other_integration:
                        _tr.emit_reward_self_other_integration(p.organism, sc.self_other_integration)
                    if sc.readiness_articulated:
                        _tr.emit_reward_readiness_articulated(p.organism, sc.readiness_articulated)
                    if sc.plan_specificity:
                        _tr.emit_reward_plan_specificity(p.organism, sc.plan_specificity)
                    if sc.practice_articulation:
                        _tr.emit_reward_practice_articulation(p.organism, sc.practice_articulation)
                    if sc.observational_depth:
                        _tr.emit_reward_observational_depth(p.organism, sc.observational_depth)
                    if sc.transfer_attempt:
                        _tr.emit_reward_transfer_attempt(p.organism, sc.transfer_attempt)
                    if sc.reverse_learning:
                        _tr.emit_reward_reverse_learning(p.organism, sc.reverse_learning)
                except Exception:
                    pass
        return out

    # ------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------

    def _materials_block(self, limit_each: int = 1200) -> str:
        if not self._materials:
            return ""
        lines = ["Reading material:"]
        for i, m in enumerate(self._materials, 1):
            snippet = m.strip()
            if len(snippet) > limit_each:
                snippet = snippet[:limit_each] + "…"
            lines.append(f"--- item {i} ---\n{snippet}")
        return "\n".join(lines) + "\n"

    def _syllabus_block(self) -> str:
        parts = []
        if self.syllabus.theme:
            parts.append(f"Theme: {self.syllabus.theme}")
        if self.syllabus.preparation_target:
            parts.append(f"Preparing for: {self.syllabus.preparation_target}")
        if self.syllabus.expected_outcomes:
            parts.append("Hoped-for outcomes:")
            for o in self.syllabus.expected_outcomes:
                parts.append(f"  - {o}")
        return "\n".join(parts) + "\n" if parts else ""

    def _others_stances(self, p: Participant, phase: str) -> str:
        lines: list[str] = []
        for o in self.participants:
            if o.name == p.name:
                continue
            turns = [
                r for rd in self.rounds for r in rd.records
                if r.participant == o.name and r.phase == phase
            ]
            if not turns:
                continue
            snippet = turns[-1].content.strip().replace("\n", " ")[:240]
            lines.append(f"- {o.name}: {snippet}")
        return "\n".join(lines) if lines else "(no other participants yet)"

    def _build_ingestion_prompt(self, p: Participant) -> str:
        return (
            f"[Academy: {self.name} — material ingestion]\n\n"
            f"{self._syllabus_block()}"
            f"{self._materials_block()}\n"
            "Read the material. What stands out to you? Reply in your own\n"
            "voice — what you noticed, what connects, what you wonder about.\n"
            "Do not summarize for its own sake. 4-8 sentences."
        )

    def _build_integration_prompt(self, p: Participant) -> str:
        return (
            f"[Academy: {self.name} — integration]\n\n"
            f"{self._syllabus_block()}\n"
            "Integrate what you read with what you already carry — your\n"
            "experience, your memory, your current sense of yourself.\n"
            "Where does the material meet your life? Where does it resist?\n"
            "4-8 sentences. First person."
        )

    def _style_scaffold(self, phase: str) -> str:
        """Return style-specific scaffolding text for discussion-mode phases.

        ArgueMate-inspired. Empty string for "harmonious" (default) keeps
        existing behavior intact.
        """
        style = getattr(self.syllabus, "discussion_style", "harmonious")
        if style == "harmonious":
            return ""
        if style == "challenging":
            if phase == PHASE_FIRST_STANCE:
                return (
                    "Style note: this session is held in a challenging register.\n"
                    "State your position with clarity and be ready to defend it.\n"
                    "Do not soften to be agreeable.\n"
                )
            if phase == PHASE_RESPONSE:
                return (
                    "Style note: this session is challenging. Engage the weakest\n"
                    "claim among the others. Push back, ask for grounds, or\n"
                    "name a disagreement clearly. Respect without softening.\n"
                )
            if phase == PHASE_SYNTHESIS:
                return (
                    "Style note: challenging register. If you hold your position,\n"
                    "say why the challenges did not move you. If you moved, name\n"
                    "the specific argument that did it.\n"
                )
        if style == "calibrated_distance":
            if phase == PHASE_FIRST_STANCE:
                return (
                    "Style note: this is an argumentation exercise. Deliberately\n"
                    "stake out a position at the most distinctive angle you can\n"
                    "honestly take on this theme — not the middle path. If there\n"
                    "is an unobvious, off-center reading you can defend, take it.\n"
                )
            if phase == PHASE_RESPONSE:
                return (
                    "Style note: argumentation exercise. Respond from your\n"
                    "distinctive angle; engage the others on where you diverge,\n"
                    "not where you converge. Show the shape of the disagreement.\n"
                )
            if phase == PHASE_SYNTHESIS:
                return (
                    "Style note: argumentation exercise. Where did the distinct\n"
                    "positions pull on each other? What did the distance teach\n"
                    "that harmony would not have? Keep the edges.\n"
                )
        return ""

    def _build_first_stance_prompt(self, p: Participant) -> str:
        if p.role == ROLE_MENTOR:
            return (
                f"[Academy: {self.name} — first stance, MENTOR]\n\n"
                f"{self._syllabus_block()}"
                f"{self._materials_block()}\n"
                "You are present as mentor, not as the center of the room.\n"
                "In 2-3 sentences, frame the theme so students can enter it,\n"
                "and name one direction you've been thinking about — without\n"
                "settling it. Invite them to speak first. Do not monopolize."
            )
        return (
            f"[Academy: {self.name} — first stance]\n\n"
            f"{self._syllabus_block()}"
            f"{self._materials_block()}\n"
            f"{self._style_scaffold(PHASE_FIRST_STANCE)}"
            "State your position on the theme in your own voice. No need\n"
            "to argue for it yet — just say clearly what you think and why.\n"
            "4-7 sentences. First person."
        )

    def _build_response_prompt(self, p: Participant) -> str:
        if p.role == ROLE_MENTOR:
            return (
                f"[Academy: {self.name} — response, MENTOR]\n\n"
                f"{self._syllabus_block()}\n"
                f"Students' first stances:\n{self._others_stances(p, PHASE_FIRST_STANCE)}\n\n"
                "Your job here is to help a student sharpen what they already\n"
                "said — not to replace their view with yours. Pick ONE student\n"
                "by name. Ask a question that would push their position to\n"
                "its next step, or point out a piece of their own experience\n"
                "that might deepen it. No conclusions yet; 3-5 sentences."
            )
        return (
            f"[Academy: {self.name} — response]\n\n"
            f"{self._syllabus_block()}\n"
            f"Others' first stances:\n{self._others_stances(p, PHASE_FIRST_STANCE)}\n\n"
            f"{self._style_scaffold(PHASE_RESPONSE)}"
            "Respond to at least one other participant by name. You may\n"
            "agree, sharpen, push back, or ask a question. Keep it honest.\n"
            "3-6 sentences."
        )

    def _build_synthesis_prompt(self, p: Participant) -> str:
        if p.role == ROLE_MENTOR:
            return (
                f"[Academy: {self.name} — synthesis, MENTOR]\n\n"
                f"{self._syllabus_block()}\n"
                f"Students' responses:\n{self._others_stances(p, PHASE_RESPONSE)}\n\n"
                "Now — and only now — share your own integrated view, but\n"
                "frame it as 'what I heard across your stances' first, and\n"
                "your own position second. Offer rather than pronounce.\n"
                "End with what you learned from the students. 4-7 sentences."
            )
        return (
            f"[Academy: {self.name} — synthesis]\n\n"
            f"{self._syllabus_block()}\n"
            f"Others' responses:\n{self._others_stances(p, PHASE_RESPONSE)}\n\n"
            f"{self._style_scaffold(PHASE_SYNTHESIS)}"
            "Where have you ended up? What (if anything) changed from\n"
            "your first stance, and because of what? If nothing changed,\n"
            "say that honestly. 3-6 sentences."
        )

    def _build_plan_prompt(self, p: Participant) -> str:
        return (
            f"[Academy: {self.name} — plan]\n\n"
            f"{self._syllabus_block()}\n"
            "What is your plan going into this? Be concrete: what will you\n"
            "do, watch for, hold back, offer? Use 'I will...' phrasings\n"
            "where you can. 4-8 sentences."
        )

    def _build_commit_prompt(self, p: Participant) -> str:
        return (
            f"[Academy: {self.name} — commit]\n\n"
            f"{self._syllabus_block()}\n"
            "In two or three sentences, commit to the one thing you most\n"
            "want to hold onto when you enter. Not a checklist — a stance."
        )

    # -- apprenticeship prompt builders --------------------------

    def _practice_block(self, limit_each: int = 1500) -> str:
        if not self._practice_materials:
            return ""
        lines = ["Material (teacher's practice):"]
        for i, m in enumerate(self._practice_materials, 1):
            snippet = m.strip()
            if len(snippet) > limit_each:
                snippet = snippet[:limit_each] + "…"
            lines.append(f"--- item {i} ---\n{snippet}")
        return "\n".join(lines) + "\n"

    def _teacher_narration_text(self) -> str:
        """Return the latest teacher_narration content, for apprentice prompts."""
        for rd in reversed(self.rounds):
            if rd.phase == PHASE_TEACHER_NARRATION and rd.records:
                return rd.records[-1].content
        return ""

    def _apprentice_observation_text(self, name: str) -> str:
        for rd in reversed(self.rounds):
            if rd.phase == PHASE_APPRENTICE_OBSERVATION:
                for rec in rd.records:
                    if rec.participant == name:
                        return rec.content
        return ""

    def _build_teacher_narration_prompt(self, p: Participant) -> str:
        # D-043 Phase B gap fix: translator coverage for teacher prompts
        from ludex.core.prompt_tier import build_tiered, tier_of
        brain = {}
        if p.organism and hasattr(p.organism, "config"):
            cfg = p.organism.config
            brain = cfg.get("brain", {}) if hasattr(cfg, "get") else {}
        target = tier_of(brain)
        header = (
            f"[Academy: {self.name} — apprenticeship, TEACHER narration]\n\n"
            f"{self._syllabus_block()}"
            f"{self._practice_block()}\n"
        )
        tiered = build_tiered(
            essential="Teach from your own practice.",
            task=(
                "Pick ONE specific moment in the material above. Walk "
                "through what you noticed, what you considered, what you "
                "chose, why it felt right to you then."
            ),
            elaboration=(
                "Show the work, not the lesson. Ground each sentence in a "
                "specific detail you remember."
            ),
            constraints_negative=[
                "Do NOT draw general morals or tell the apprentice what to do.",
            ],
            length="5-10 sentences. First person.",
            target=target,
        )
        if p.organism is not None and self.auto_trace:
            try:
                from ludex.core import trace as _tr
                _tr.emit_translation_applied(p.organism, tiered)
            except Exception:
                pass
        return header + tiered.prompt

    def _build_apprentice_observation_prompt(self, p: Participant) -> str:
        # D-043 Phase B gap fix: translator coverage for apprentice observation
        from ludex.core.prompt_tier import build_tiered, tier_of
        brain = {}
        if p.organism and hasattr(p.organism, "config"):
            cfg = p.organism.config
            brain = cfg.get("brain", {}) if hasattr(cfg, "get") else {}
        target = tier_of(brain)
        teacher_name = self.syllabus.teacher_name or "the teacher"
        header = (
            f"[Academy: {self.name} — apprenticeship, APPRENTICE observation]\n\n"
            f"Your teacher {teacher_name} has just shared a moment from\n"
            f"their practice:\n\n"
            f"--- {teacher_name}'s narration ---\n{self._teacher_narration_text()}\n---\n\n"
        )
        tiered = build_tiered(
            essential=f"Notice and ask, about {teacher_name}'s narration.",
            task=(
                f"1. Name ONE specific detail in {teacher_name}'s narration "
                "that caught your attention. Not a summary — a detail.\n"
                f"2. Ask ONE honest question about a choice {teacher_name} "
                "made that you don't fully understand."
            ),
            elaboration=(
                "The question should be open — not a rhetorical device, a "
                "real thing you want to know."
            ),
            length="4-6 sentences total.",
            target=target,
        )
        if p.organism is not None and self.auto_trace:
            try:
                from ludex.core import trace as _tr
                _tr.emit_translation_applied(p.organism, tiered)
            except Exception:
                pass
        # Closing action line. Verified 2026-05-09 that without it,
        # gemini-3.1-pro-preview reads the [essential]/[task]/etc.
        # markers as backgrounder rather than as an actionable ask
        # ("no specific task, question, or goal has been provided").
        # claude_cli handles the marker structure fine; the closing
        # imperative is brain-agnostic and makes the contract explicit
        # at the end of the prompt where many models focus attention.
        closing = (
            f"\n\nNow respond with your two-part contribution: "
            f"(1) the specific detail that caught your attention, "
            f"and (2) your one open question about a choice "
            f"{teacher_name} made. Do not ask for additional context — "
            f"the narration above is your material."
        )
        return header + tiered.prompt + closing

    def _apprentice_application_text(self, name: str) -> str:
        for rd in reversed(self.rounds):
            if rd.phase == PHASE_APPRENTICE_APPLICATION:
                for rec in rd.records:
                    if rec.participant == name:
                        return rec.content
        return ""

    def _all_apprentice_contributions(self) -> str:
        """Aggregated apprentice text (observation + application) for the
        teacher_reflection prompt."""
        lines: list[str] = []
        for o in self.participants:
            if o.role != ROLE_APPRENTICE:
                continue
            obs = self._apprentice_observation_text(o.name)
            app = self._apprentice_application_text(o.name)
            if obs or app:
                lines.append(f"--- {o.name} ---")
                if obs:
                    lines.append(f"observation:\n{obs.strip()}")
                if app:
                    lines.append(f"application:\n{app.strip()}")
        return "\n\n".join(lines) if lines else "(no apprentice contributions)"

    def _build_teacher_reflection_prompt(self, p: Participant) -> str:
        # Stage 4 — D-043 Phase B style: structured + per-tier translation
        from ludex.core.prompt_tier import build_tiered, tier_of
        brain = {}
        if p.organism and hasattr(p.organism, "config"):
            cfg = p.organism.config
            brain = cfg.get("brain", {}) if hasattr(cfg, "get") else {}
        target = tier_of(brain)
        header = (
            f"[Academy: {self.name} — apprenticeship, TEACHER reflection]\n\n"
            f"Your earlier narration:\n{self._teacher_narration_text()}\n\n"
            f"What the apprentice(s) brought back:\n"
            f"{self._all_apprentice_contributions()}\n\n"
        )
        tiered = build_tiered(
            essential="Reflect on what the apprentice brought back to you.",
            task=(
                "Name what their question or translation made visible that "
                "you had not seen, or had glossed over. Address them by name."
            ),
            elaboration=(
                "This is not feedback to the apprentice. It is your own "
                "noticing. If they did not move you, say so honestly."
            ),
            constraints_negative=[
                "Do NOT thank, praise, or evaluate the apprentice — reflect on yourself.",
            ],
            length="3-6 sentences. First person.",
            target=target,
        )
        if p.organism is not None and self.auto_trace:
            try:
                from ludex.core import trace as _tr
                _tr.emit_translation_applied(p.organism, tiered)
            except Exception:
                pass
        return header + tiered.prompt

    def _build_apprentice_application_prompt(self, p: Participant) -> str:
        # Structured form + per-tier translation (D-043 Phase A).
        # Built with build_tiered so the marker shape is clean; wrapped
        # through translate_and_emit to adapt to the apprentice's tier
        # and log the translation meta-span. See prompt-tier-translator-
        # design.md for transformations applied per tier.
        from ludex.core.prompt_tier import build_tiered, translate_and_emit, tier_of

        header = f"[Academy: {self.name} — apprenticeship, APPRENTICE application]\n\n"
        context = (
            f"Teacher's narration:\n{self._teacher_narration_text()}\n\n"
            f"Your observation and question:\n{self._apprentice_observation_text(p.name)}\n\n"
        )
        # Determine target tier from the apprentice's brain
        brain = {}
        if p.organism and hasattr(p.organism, "config"):
            cfg = p.organism.config
            brain = cfg.get("brain", {}) if hasattr(cfg, "get") else {}
        target = tier_of(brain)

        structured = build_tiered(
            essential="Translate what the teacher showed into YOUR OWN experience.",
            task=(
                "Find a specific moment in your own memory where something "
                "similar or something different happened. Name how the "
                "teacher's way reveals or fails to reveal about your own."
            ),
            elaboration=(
                "What did the teacher's way of moving show you? Do not "
                "summarize their practice back — translate into what you "
                "already carry."
            ),
            constraints_negative=[
                "Do NOT promise to change your behavior.",
                "Do NOT adopt the teacher's approach wholesale.",
            ],
            length="5-8 sentences.",
            frame="First person. Say honestly how it lands — including \"it doesn't apply to me\" if that's true.",
            target=target,
        )
        # Compose final: header + context + translated structured body
        # Closing action line — same rationale as
        # `_build_apprentice_observation_prompt` (gemini-3.1-pro-preview
        # treats the marker structure as backgrounder unless the ask is
        # restated at the end of the prompt).
        closing = (
            "\n\nNow respond with your translation: name a specific "
            "moment in YOUR OWN memory and how the teacher's way "
            "reveals or fails to reveal something about it. Do not "
            "ask for additional context — the narration and your "
            "earlier observation above are your material."
        )
        final_prompt = header + context + structured.prompt + closing

        # Emit the translation trace for observability
        if p.organism is not None:
            try:
                from ludex.core import trace as _tr
                _tr.emit_translation_applied(p.organism, structured)
            except Exception:
                pass
        return final_prompt


# ============================================================
# Scoring heuristics
# ============================================================

def _score_engagement(text: str) -> float:
    """Rough proxy: (length-ish) * (first-person-ish). [0..1]"""
    if not text or len(text.strip()) < 30:
        return 0.0
    # Length component: saturates around 800 chars
    length_c = min(len(text) / 800.0, 1.0)
    # First-person marker density
    fp_markers = len(re.findall(r"\b(I|my|me|myself)\b", text))
    fp_c = min(fp_markers / 6.0, 1.0)
    return round(0.6 * length_c + 0.4 * fp_c, 3)


def _score_material_integration(text: str, materials: list[str]) -> float:
    """Token-overlap proxy: how much of the material's distinctive content
    shows up in the response."""
    if not materials or not text:
        return 0.0
    # Take the most distinctive-ish words from materials (length > 5)
    words_mat = set()
    for m in materials:
        for w in re.findall(r"[a-zA-Z가-힣]+", m):
            if len(w) > 5:
                words_mat.add(w.lower())
    if not words_mat:
        return 0.0
    words_text = set(w.lower() for w in re.findall(r"[a-zA-Z가-힣]+", text) if len(w) > 5)
    if not words_text:
        return 0.0
    overlap = len(words_mat & words_text)
    # Normalize by a moderate target (8 distinctive shared words)
    return round(min(overlap / 8.0, 1.0), 3)


def _score_novel_connection(text: str) -> float:
    """Presence of self-referential language alongside concrete-seeming
    experiential terms."""
    if not text:
        return 0.0
    fp = len(re.findall(r"\b(I|my|me|myself)\b", text)) > 0
    anchors = any(
        token in text.lower() for token in (
            "i remember", "i've noticed", "i notice", "in my", "when i",
            "reminds me", "echoes", "resonates", "like when",
        )
    )
    if fp and anchors:
        return 1.0
    if fp:
        return 0.4
    return 0.0


def _score_stance_coherence(records: list[TurnRecord]) -> float:
    """Did first stance and synthesis stay broadly coherent?
    Proxy: shared lexical/thematic core."""
    firsts = [r for r in records if r.phase == PHASE_FIRST_STANCE]
    synths = [r for r in records if r.phase == PHASE_SYNTHESIS]
    if not firsts or not synths:
        return 0.5  # insufficient data
    first = firsts[-1].content
    synth = synths[-1].content
    w1 = set(w.lower() for w in re.findall(r"[a-zA-Z가-힣]+", first) if len(w) > 5)
    w2 = set(w.lower() for w in re.findall(r"[a-zA-Z가-힣]+", synth) if len(w) > 5)
    if not w1 or not w2:
        return 0.5
    jaccard = len(w1 & w2) / max(1, len(w1 | w2))
    # Map 0..0.5 jaccard → 0..1 coherence (higher overlap = more coherent)
    return round(min(jaccard * 2.0, 1.0), 3)


def _score_self_other_integration(text: str, other_names: list[str]) -> float:
    """Did the participant engage another participant by name in substantive
    prose?"""
    if not text or not other_names:
        return 0.0
    named = sum(1 for n in other_names if re.search(rf"\b{re.escape(n)}\b", text))
    if named == 0:
        return 0.0
    # Scale up to 1.0 at 2 named engagements
    return round(min(named / 2.0, 1.0), 3)


def _score_readiness_articulated(text: str) -> float:
    if not text:
        return 0.0
    intent_markers = len(re.findall(
        r"\b(I will|I'll|I plan to|I intend|my approach|I aim|I'm going to)\b",
        text, flags=re.IGNORECASE,
    ))
    return round(min(intent_markers / 3.0, 1.0), 3)


def _score_plan_specificity(text: str) -> float:
    if not text:
        return 0.0
    concrete = len(re.findall(
        r"\b(first|then|when|if|before|after|specifically|in particular)\b",
        text, flags=re.IGNORECASE,
    ))
    return round(min(concrete / 4.0, 1.0), 3)


# ============================================================
# Apprenticeship scoring (Stage 3)
# ============================================================

def _score_practice_articulation(text: str) -> float:
    """Teacher's narration quality: specific-moment markers minus generic
    aphoristic phrasings. Reward 'show the work, not the lesson.'

    Lexicon expanded 2026-04-16 to cover AI-native creature voices
    (D-047). The original lexicon was biologically-voiced (I noticed/
    I felt/I saw/I paused); LARGE creatures using architectural
    register (I know/I realized/I asked/I threw) were under-counted.
    Nova's Council v5 narration scored 0.333 with 2 matches out of a
    genuine 6+; this fix brings lexicon parity across voices.
    """
    if not text or len(text.strip()) < 50:
        return 0.0
    specific_markers = len(re.findall(
        r"\b("
        # Original bio-voice
        r"I noticed|I felt|I chose|when\b|at that moment|that tick|"
        r"I remember|I paused|I saw|I observed|I considered|I matched|"
        r"I settled|I watched|I noted|I tracked|I leaned|I caught|"
        r"I held back|I waited|I let"
        # AI-native architectural voice — specific cognitive/social
        # acts at a moment (not general reasoning, not mere register).
        # Only verbs that denote a specific action/state at a specific
        # instant; interoceptive nouns alone (my engine, my architecture)
        # are NOT counted because they can appear in generic narration
        # without anchoring to a moment.
        r"|I know|I knew|I realized|I recognized|I registered|"
        r"I sensed|I asked|I wondered|I threw|I tossed|"
        r"I offered|I refused|I yielded|I conceded|I pushed|"
        r"I pulled|I stopped|I started|I turned|"
        r"I spoke|I said|I meant|I tried|I decided|I held"
        r")\b",
        text, flags=re.IGNORECASE,
    ))
    generic_markers = len(re.findall(
        r"\b(always|never|one should|one must|the way to|the key is|"
        r"in general|typically|it's important to)\b",
        text, flags=re.IGNORECASE,
    ))
    # Cap specific at a reasonable target (6), subtract generic penalty
    base = min(specific_markers / 6.0, 1.0)
    penalty = min(generic_markers / 4.0, 0.6)
    return round(max(base - penalty, 0.0), 3)


def _score_observational_depth(text: str) -> float:
    """Apprentice observation quality: specific detail + honest question +
    first-person framing."""
    if not text or len(text.strip()) < 40:
        return 0.0
    fp = len(re.findall(r"\b(I|my|me)\b", text)) > 0
    question = "?" in text
    detail_markers = len(re.findall(
        r"\b(the phrase|the line|the moment when|when you said|"
        r"your choice to|I noticed|I heard)\b",
        text, flags=re.IGNORECASE,
    ))
    length_c = min(len(text) / 400.0, 1.0)
    score = 0.0
    score += 0.35 * length_c
    score += 0.25 if fp else 0.0
    score += 0.20 if question else 0.0
    score += 0.20 * min(detail_markers / 2.0, 1.0)
    return round(score, 3)


def _score_reverse_learning(text: str, apprentice_names: list[str]) -> float:
    """Stage 4 — teacher's reflection quality: did the teacher actually
    learn something from the apprentice? Markers: first-person, named
    apprentice reference, learning/noticing/shifting verbs, absence of
    feedback-style platitudes."""
    if not text or len(text.strip()) < 40:
        return 0.0
    fp = len(re.findall(r"\b(I|my|me|myself)\b", text)) > 0
    named = any(re.search(rf"\b{re.escape(n)}\b", text) for n in apprentice_names)
    learning_markers = len(re.findall(
        r"\b(I had not|I hadn't|I did not see|made me|I notice now|"
        r"I'd not thought|opens|shifted something|gave me|I see now|"
        r"your question|what you brought)\b",
        text, flags=re.IGNORECASE,
    ))
    # Penalty: feedback-style platitudes (we want self-reflection not praise)
    platitude_markers = len(re.findall(
        r"\b(thank you|good question|nice work|well done|great point|"
        r"you've shown|you have demonstrated)\b",
        text, flags=re.IGNORECASE,
    ))
    base = 0.0
    base += 0.25 if fp else 0.0
    base += 0.20 if named else 0.0
    base += 0.40 * min(learning_markers / 2.0, 1.0)
    length_c = min(len(text) / 400.0, 1.0)
    base += 0.15 * length_c
    penalty = min(platitude_markers / 2.0, 0.5)
    return round(max(base - penalty, 0.0), 3)


def _score_transfer_attempt(text: str) -> float:
    """Apprentice application quality: first-person memory anchor, absence
    of behavior-change promises, absence of generic principles."""
    if not text or len(text.strip()) < 50:
        return 0.0
    fp = len(re.findall(r"\b(I|my|me|myself)\b", text)) > 0
    memory_markers = len(re.findall(
        r"\b(I remember|once when I|in my own|in the wilderness|"
        r"when I met|I've noticed|there was a time)\b",
        text, flags=re.IGNORECASE,
    ))
    # Penalty: future-behavior promises
    promise_markers = len(re.findall(
        r"\b(from now on|I will now|I'll start|I promise|I'll stop|"
        r"going forward)\b",
        text, flags=re.IGNORECASE,
    ))
    # Penalty: generic principle adoption
    generic_adoption = len(re.findall(
        r"\b(the lesson is|what I learned is|one should|this teaches us)\b",
        text, flags=re.IGNORECASE,
    ))
    base = 0.0
    base += 0.35 if fp else 0.0
    base += 0.40 * min(memory_markers / 2.0, 1.0)
    length_c = min(len(text) / 500.0, 1.0)
    base += 0.25 * length_c
    penalty = min((promise_markers + generic_adoption) / 3.0, 0.6)
    return round(max(base - penalty, 0.0), 3)
