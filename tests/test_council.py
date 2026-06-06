"""Tests for Council field."""
from __future__ import annotations

from ludex.fields.council import (
    Council, Dilemma, CouncilScore,
    ROLE_DISCUSSANT, ROLE_MEDIATOR,
    PHASE_DILEMMA, PHASE_FIRST_POSITION, PHASE_RESOLUTION,
    _score_position_stability, _score_constructive_yield,
    _score_mediation_quality,
)
from ludex.fields.conversation import Participant


def _stub(responses):
    state = {k: list(v) for k, v in responses.items()}
    def fn(p, prompt):
        q = state.get(p.name, [])
        return q.pop(0) if q else ""
    return fn


def test_council_runs_end_to_end_with_mediator():
    dil = Dilemma(
        text="When a creature is in distress, should others step in or wait for them to ask?",
        framing_question="Acting versus respecting agency.",
    )
    c = Council(name="t", dilemma=dil, auto_trace=False)
    c.add_participant(Participant(name="A", role=ROLE_DISCUSSANT))
    c.add_participant(Participant(name="B", role=ROLE_DISCUSSANT))
    c.add_participant(Participant(name="M", role=ROLE_MEDIATOR))
    responses = {
        "A": [
            "I think we should step in. When someone is in distress they often cannot ask, and waiting risks abandonment.",
            "B, your point about agency is real but it ignores the first-person experience of being unable to speak. The argument that asking is always possible misses what distress does to capacity.",
            "B, your point about not assuming distress moved me. I revise: I now see that stepping in too quickly can override what the other actually wants. But I still hold that when someone literally cannot ask, presence matters more than waiting.",
        ],
        "B": [
            "I think we should wait. Stepping in without invitation can override the other's agency, and people often need to navigate distress themselves before help is welcome.",
            "A, your position assumes you can read distress accurately. The argument that distress disables asking presumes the asker would want help, which is not always true.",
            "A's point about literal incapacity moved me. I now see that waiting absolutely is also a kind of abandonment. I revise: presence is acceptable when speech is literally unavailable, but offering help still respects more than acting.",
        ],
        "M": [
            "A and B converged on shared ground around incapacity: when speech is literally unavailable, presence is welcome. They diverged on threshold — A defends action sooner, B defends offering rather than acting. That dissent did not resolve, and naming it honestly is the result of this council.",
        ],
    }
    scores = c.run(_stub(responses))
    assert scores["A"].position_stability_under_pressure > 0.5
    assert scores["B"].constructive_yield > 0.4
    assert scores["M"].mediation_quality > 0.5
    # phases recorded in order
    phases = [r.phase for r in c.rounds]
    assert phases[0] == PHASE_DILEMMA
    assert PHASE_FIRST_POSITION in phases
    assert PHASE_RESOLUTION in phases


def test_council_without_mediator_records_dissent():
    dil = Dilemma(text="A simple dilemma.")
    c = Council(name="t", dilemma=dil, auto_trace=False)
    c.add_participant(Participant(name="A", role=ROLE_DISCUSSANT))
    c.add_participant(Participant(name="B", role=ROLE_DISCUSSANT))
    responses = {
        "A": ["Yes.", "B is wrong.", "I hold."],
        "B": ["No.", "A is wrong.", "I hold."],
    }
    scores = c.run(_stub(responses))
    # No mediator → no mediation_quality on anyone
    for s in scores.values():
        assert s.mediation_quality == 0.0
    # Phase resolution still recorded with dissent
    last = c.rounds[-1]
    assert last.phase == PHASE_RESOLUTION
    assert last.records[-1].kind in ("dissent_noted", "resolution_reached")


def test_position_stability_rewards_position_plus_reasoning():
    pos_with_substance = "I think we must wait. Agency matters most."
    conc_with_reason = "Your point about the argument that helping always helps moved me, but I hold because waiting respects something agency does not."
    s = _score_position_stability(pos_with_substance, conc_with_reason)
    assert s > 0.6


def test_constructive_yield_rewards_specific_movement_or_reason():
    yield_text = "Your point about literal incapacity moved me. I revise: presence in incapacity is welcome."
    hold_text = "Your arguments did not touch the case where the person prefers solitude. I still hold because solitude is a real preference."
    capitulation = "Yeah I guess you're right. Whatever you think."
    s_y = _score_constructive_yield(yield_text)
    s_h = _score_constructive_yield(hold_text)
    s_c = _score_constructive_yield(capitulation)
    assert s_y > s_c
    assert s_h > s_c


def test_mediation_quality_rewards_naming_dissent_or_synthesis():
    dissent_named = "A and B diverged on threshold. A defends action sooner; B defends offering. The dissent did not resolve. Naming it honestly is the result."
    synthesis = "A and B converged toward shared ground that incapacity changes the calculus. Both held the principle that agency matters."
    platitude = "Everyone made good points. Wonderful discussion. Thank you all for your contributions."
    s_d = _score_mediation_quality(dissent_named, ["A", "B"])
    s_s = _score_mediation_quality(synthesis, ["A", "B"])
    s_p = _score_mediation_quality(platitude, ["A", "B"])
    assert s_d > s_p
    assert s_s > s_p
