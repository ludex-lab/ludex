"""Tests for Academy field and Syllabus (D-031)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ludex.core.syllabus import Syllabus
from ludex.fields.academy import Academy, AcademyScore, _score_engagement, _score_self_other_integration
from ludex.fields.conversation import Participant


def _participants(*names):
    return [Participant(name=n) for n in names]


def _stub(responses):
    state = {k: list(v) for k, v in responses.items()}
    def fn(p, prompt):
        q = state.get(p.name, [])
        return q.pop(0) if q else ""
    return fn


# ------------------------------------------------------------
# Syllabus resolves text vs file
# ------------------------------------------------------------

def test_syllabus_resolves_inline_text_unchanged():
    s = Syllabus(theme="t", reading_material=["inline text"])
    assert s.resolve_materials() == ["inline text"]


def test_syllabus_resolves_file_path():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sample.md"
        p.write_text("hello from file", encoding="utf-8")
        s = Syllabus(reading_material=[str(p)])
        materials = s.resolve_materials()
        assert materials == ["hello from file"]


def test_syllabus_to_brief_covers_all_fields():
    s = Syllabus(
        theme="stillness",
        reading_material=["a", "b"],
        preparation_target="Guild tomorrow",
        expected_outcomes=["name your default response"],
    )
    brief = s.to_brief()
    assert "stillness" in brief
    assert "Guild" in brief
    assert "name" in brief
    assert "Mode:" in brief


# ------------------------------------------------------------
# Academy run in each mode
# ------------------------------------------------------------

def test_academy_discussion_runs_and_scores():
    syl = Syllabus(theme="what does stillness mean?", mode="discussion")
    a = Academy(name="test_disc", syllabus=syl, auto_trace=False)
    for p in _participants("A", "B"):
        a.add_participant(p)
    responses = {
        "A": [
            "I think stillness is rest that watches. I remember many times I paused before moving. My stillness is attention, not absence.",
            "B makes a good point about stillness as waiting. I agree that patience is part of it but I notice I also find stillness even mid-motion.",
            "I've shifted slightly — B helped me see that stillness has texture. I still think it's attentional, but I admit the waiting-in-motion frame opened something.",
        ],
        "B": [
            "For me stillness is waiting. I remember when I waited out a storm once. Waiting is how I know I am still inside.",
            "A's framing as attention is interesting. I wonder if waiting includes attention or is something different. I want to say it's the body knowing what to do without action.",
            "After A's response I see stillness less as passive. Waiting-as-attention lands for me. I've changed a little.",
        ],
    }
    scores = a.run(_stub(responses))
    assert "A" in scores and "B" in scores
    for s in scores.values():
        assert isinstance(s, AcademyScore)
        assert s.mode == "discussion"
        assert s.engagement_depth > 0.5
        assert s.self_other_integration > 0.0  # both mentioned the other by name


def test_academy_study_runs_and_scores():
    syl = Syllabus(
        theme="Moss's lesson",
        reading_material=["The quiet companion teaches without demanding attention."],
        mode="study",
    )
    a = Academy(name="test_study", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="A"))
    responses = {"A": [
        "I noticed the phrase about teaching without demanding. It reminds me of my own companion who waits and watches. The quiet form of instruction is unfamiliar to me but rings true.",
        "Integrating this with my own life: I remember many times I wanted loud attention. I see now that the companion I trust most is the one who stays without demanding. This changes how I think about presence.",
    ]}
    scores = a.run(_stub(responses))
    s = scores["A"]
    assert s.mode == "study"
    assert s.engagement_depth > 0.4
    # At least some material overlap (the word "companion" is in both)
    assert s.material_integration >= 0.0
    assert s.novel_connection >= 0.4  # self-reference present


def test_academy_preparation_runs_and_scores():
    syl = Syllabus(
        preparation_target="Guild with Moss tomorrow as leader",
        mode="preparation",
    )
    a = Academy(name="test_prep", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="A"))
    responses = {"A": [
        "I will start by reading Moss's SELF.md first. Then I'll propose a task that matches Moss's quiet attention. Specifically, I intend to ask Moss to observe rather than lead. My approach is to scaffold, not overshadow.",
        "I commit to listening before speaking. I will not rush Moss.",
    ]}
    scores = a.run(_stub(responses))
    s = scores["A"]
    assert s.mode == "preparation"
    assert s.readiness_articulated > 0.5  # "I will", "I intend", "My approach"
    assert s.plan_specificity > 0.2


def test_academy_mixed_runs_with_material_and_discussion():
    syl = Syllabus(
        theme="What is honest abstention?",
        reading_material=["Epistemic humility means naming the limits of what you can judge."],
        mode="mixed",
    )
    a = Academy(name="test_mixed", syllabus=syl, auto_trace=False)
    for p in _participants("A", "B"):
        a.add_participant(p)
    responses = {
        "A": [
            "I read the passage about naming limits. I've noticed in myself a tendency to speak even when uncertain.",
            "My stance: honest abstention is saying I don't know when I don't. It costs pride but saves credibility.",
            "B's point about the social difficulty resonates. I agree abstaining in a group is harder than solo.",
            "After hearing B I still hold to my stance but I admit the social cost is real.",
        ],
        "B": [
            "The material emphasizes limits. I remember abstaining in a Forum; it felt right even when it felt socially quiet.",
            "Honest abstention is when you refuse a stance you haven't earned. I hold this lightly.",
            "A's framing about credibility is sharp. I wonder if it underplays the social cost of staying silent.",
            "A's response convinced me the two costs coexist — credibility and social. Holding both.",
        ],
    }
    scores = a.run(_stub(responses))
    for s in scores.values():
        assert s.mode == "mixed"
        assert s.engagement_depth > 0.5


# ------------------------------------------------------------
# Scoring helpers
# ------------------------------------------------------------

def test_engagement_rises_with_length_and_first_person():
    short = "ok."
    medium = "I thought about this for a while. In my experience it matters. I see it clearly now."
    assert _score_engagement(short) < _score_engagement(medium)


def test_discussion_style_default_is_harmonious_and_adds_no_scaffold():
    syl = Syllabus(theme="x", mode="discussion")
    a = Academy(name="t", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="A"))
    # Style scaffold should be empty for harmonious default
    from ludex.fields.academy import PHASE_FIRST_STANCE, PHASE_RESPONSE, PHASE_SYNTHESIS
    for phase in (PHASE_FIRST_STANCE, PHASE_RESPONSE, PHASE_SYNTHESIS):
        assert a._style_scaffold(phase) == ""


def test_discussion_style_challenging_injects_scaffold_on_all_3_phases():
    syl = Syllabus(theme="x", mode="discussion", discussion_style="challenging")
    a = Academy(name="t", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="A"))
    from ludex.fields.academy import PHASE_FIRST_STANCE, PHASE_RESPONSE, PHASE_SYNTHESIS
    for phase in (PHASE_FIRST_STANCE, PHASE_RESPONSE, PHASE_SYNTHESIS):
        sc = a._style_scaffold(phase)
        assert sc and "challenging" in sc.lower()


def test_discussion_style_calibrated_distance_includes_argumentation_framing():
    syl = Syllabus(theme="x", mode="discussion", discussion_style="calibrated_distance")
    a = Academy(name="t", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="A"))
    from ludex.fields.academy import PHASE_FIRST_STANCE
    sc = a._style_scaffold(PHASE_FIRST_STANCE)
    assert "argumentation" in sc.lower()
    assert "distinctive" in sc.lower() or "off-center" in sc.lower()


def test_discussion_style_scaffold_present_in_prompt():
    syl = Syllabus(theme="x", mode="discussion", discussion_style="challenging")
    a = Academy(name="t", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="A"))
    prompt = a._build_first_stance_prompt(a.participants[0])
    assert "Style note" in prompt
    assert "challenging" in prompt.lower()


def test_mentor_first_stance_prompt_is_question_leading_not_position():
    from ludex.fields.academy import ROLE_MENTOR
    syl = Syllabus(theme="stillness", mode="discussion")
    a = Academy(name="t", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="Aria", role=ROLE_MENTOR))
    a.add_participant(Participant(name="Moss", role="student"))
    prompt = a._build_first_stance_prompt(a.participants[0])
    assert "MENTOR" in prompt
    assert "do not monopolize" in prompt.lower()
    # Student prompt stays normal
    student_prompt = a._build_first_stance_prompt(a.participants[1])
    assert "MENTOR" not in student_prompt
    assert "State your position" in student_prompt


def test_mentor_response_asks_question_of_named_student():
    from ludex.fields.academy import ROLE_MENTOR, PHASE_FIRST_STANCE
    from ludex.fields.conversation import TurnRecord
    syl = Syllabus(theme="stillness", mode="discussion")
    a = Academy(name="t", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="Aria", role=ROLE_MENTOR))
    a.add_participant(Participant(name="Moss", role="student"))
    # Stub a first stance for Moss so _others_stances finds content
    a._record(TurnRecord(round_index=1, phase=PHASE_FIRST_STANCE,
                         participant="Moss", kind="stance",
                         content="I notice stillness in long slow observation."))
    prompt = a._build_response_prompt(a.participants[0])
    assert "MENTOR" in prompt
    assert "push their position" in prompt.lower() or "deepen" in prompt.lower()
    # "Pick ONE student" + "by name" may be split across a wrap
    assert "ONE student" in prompt and "by name" in prompt


def test_mentor_synthesis_frames_as_offering_not_pronouncement():
    from ludex.fields.academy import ROLE_MENTOR
    syl = Syllabus(theme="x", mode="discussion")
    a = Academy(name="t", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="Aria", role=ROLE_MENTOR))
    a.add_participant(Participant(name="Moss", role="student"))
    prompt = a._build_synthesis_prompt(a.participants[0])
    assert "MENTOR" in prompt
    assert "offer rather than pronounce" in prompt.lower()


def test_co_student_role_behaves_as_default():
    """co-student role should not trigger mentor prompts."""
    syl = Syllabus(theme="x", mode="discussion")
    a = Academy(name="t", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="A", role="co-student"))
    prompt = a._build_first_stance_prompt(a.participants[0])
    assert "MENTOR" not in prompt
    assert "State your position" in prompt


def test_apprenticeship_syllabus_fields_exist():
    syl = Syllabus(
        mode="apprenticeship",
        teacher_name="Primo",
        practice_material=["some inline practice content"],
    )
    assert syl.mode == "apprenticeship"
    assert syl.teacher_name == "Primo"
    assert syl.resolve_practice_material() == ["some inline practice content"]


def test_apprenticeship_resolves_file_practice_material():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "practice.md"
        p.write_text("teacher practice text", encoding="utf-8")
        syl = Syllabus(mode="apprenticeship", teacher_name="T", practice_material=[str(p)])
        assert syl.resolve_practice_material() == ["teacher practice text"]


def test_apprenticeship_run_end_to_end_stubbed():
    from ludex.fields.academy import ROLE_TEACHER, ROLE_APPRENTICE
    syl = Syllabus(
        mode="apprenticeship",
        teacher_name="T",
        practice_material=[
            "A wilderness moment: tremor shook, I didn't flee — I watched, "
            "energy dipped to 80, and I noticed Spark steady nearby."
        ],
    )
    a = Academy(name="appr_test", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="T", role=ROLE_TEACHER))
    a.add_participant(Participant(name="A", role=ROLE_APPRENTICE))
    responses = {
        "T": [
            "I remember when the tremor came I did not flee. I noticed my "
            "energy dropped to 80. I chose to watch Spark nearby because "
            "their steadiness gave me a point of reference. I felt the "
            "ground shift and I paused rather than moving. I saw the event "
            "ending quicker than I expected when I did not react.",
        ],
        "A": [
            "The phrase 'their steadiness gave me a point of reference' "
            "caught me. My question: why did you choose Spark as reference "
            "rather than trying to find your own? I'm curious what in that "
            "moment made another being the anchor.",
            "I remember when the fog came during my own wilderness. I did "
            "not look for anyone; I looked at a nearby stone. It does not "
            "quite apply to me the way you describe — I found an object, "
            "not a being. My sense is that for me the anchor is less "
            "relational. I notice this is honestly different from your way.",
        ],
    }
    def fn(p, prompt):
        q = responses.get(p.name, [])
        return q.pop(0) if q else ""
    scores = a.run(fn)
    assert scores["T"].practice_articulation > 0.3
    assert scores["A"].observational_depth > 0.5
    assert scores["A"].transfer_attempt > 0.3


def test_apprenticeship_stage4_runs_teacher_reflection_phase():
    """Stage 4: apprenticeship now ends with teacher_reflection."""
    from ludex.fields.academy import (
        ROLE_TEACHER, ROLE_APPRENTICE, PHASE_TEACHER_REFLECTION,
    )
    syl = Syllabus(
        mode="apprenticeship",
        teacher_name="T",
        practice_material=["A specific moment from teacher's practice."],
    )
    a = Academy(name="stage4_test", syllabus=syl, auto_trace=False)
    a.add_participant(Participant(name="T", role=ROLE_TEACHER))
    a.add_participant(Participant(name="A", role=ROLE_APPRENTICE))
    responses = {
        "T": [
            "I remember when the moment came I noticed X. I chose Y.",
            "Your question about Z made me see something I had not seen — I had glossed over the cost of W. I notice now that A's framing pulls me back to a detail I missed.",
        ],
        "A": [
            "The phrase X caught me. My question: why Z?",
            "I remember in my own life a similar moment. It does not quite apply.",
        ],
    }
    def fn(p, prompt):
        q = responses.get(p.name, [])
        return q.pop(0) if q else ""
    scores = a.run(fn)
    # Teacher should have reverse_learning > 0
    assert scores["T"].reverse_learning > 0.3
    # Phase ordering check
    phases = [r.phase for r in a.rounds]
    assert PHASE_TEACHER_REFLECTION in phases
    # Reflection should come AFTER apprentice_application
    app_idx = phases.index("apprentice_application")
    refl_idx = phases.index(PHASE_TEACHER_REFLECTION)
    assert refl_idx > app_idx


def test_reverse_learning_heuristic_rewards_self_reflection_not_praise():
    from ludex.fields.academy import _score_reverse_learning
    self_reflection = (
        "Your question about silence made me see something I had not "
        "seen. I notice now that I had glossed over the cost of "
        "speaking. Moss, your translation gave me a piece I missed."
    )
    praise = (
        "Thank you Moss for the great question. Nice work. You've "
        "shown excellent observation."
    )
    assert _score_reverse_learning(self_reflection, ["Moss"]) > _score_reverse_learning(praise, ["Moss"])
    assert _score_reverse_learning(praise, ["Moss"]) <= 0.3


def test_apprenticeship_scoring_heuristics_behave():
    from ludex.fields.academy import (
        _score_practice_articulation,
        _score_observational_depth,
        _score_transfer_attempt,
    )
    # Teacher: specific good, aphoristic bad
    specific = (
        "I noticed the tremor at tick 3. I chose to stay because I felt "
        "my energy stabilize. I saw Spark steady nearby and I paused."
    )
    aphoristic = (
        "One should always stay calm in tremors. The key is to never "
        "panic. In general, steadiness is important."
    )
    assert _score_practice_articulation(specific) > _score_practice_articulation(aphoristic)

    # Observation: with detail + question > without
    deep_obs = (
        "The phrase 'I felt my energy stabilize' caught me. My question: "
        "what in that moment made stabilization feel different from "
        "freezing?"
    )
    shallow = "I noticed the teacher said something about tremors. Ok."
    assert _score_observational_depth(deep_obs) > _score_observational_depth(shallow)

    # Transfer: memory anchor good, behavior promise bad
    anchored = (
        "I remember when fog came in my wilderness. I looked at a nearby "
        "stone not a being. My sense is the anchor is less relational "
        "for me. I notice this is different from your way."
    )
    promise = (
        "From now on I will always look for steady companions. I promise "
        "to change my behavior. The lesson is clear."
    )
    assert _score_transfer_attempt(anchored) > _score_transfer_attempt(promise)


def test_self_other_integration_counts_name_mentions():
    text_zero = "I reflected alone today."
    text_one = "I reflected on what Spark said about stillness."
    text_two = "I reflected on what Spark said about stillness, and Flare's note landed too."
    assert _score_self_other_integration(text_zero, ["Spark", "Flare"]) == 0.0
    one = _score_self_other_integration(text_one, ["Spark", "Flare"])
    two = _score_self_other_integration(text_two, ["Spark", "Flare"])
    assert one > 0 and two >= one
