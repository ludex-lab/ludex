"""Tests for OpenCouncil field — §H.4 off-Council deliberation control.

Validates that the field plumbing works (turn ordering, prompt
plumbing, transcript structure) without burning brain quota. Real-
substrate validation happens in the actual §H.4 sessions.
"""
from __future__ import annotations

from ludex.fields.conversation import Participant
from ludex.fields.council import Dilemma
from ludex.fields.open_council import (
    OpenCouncil,
    PHASE_OPEN_DELIBERATION,
    KIND_DILEMMA_POSED,
    KIND_TURN_TAKEN,
)


def _stub(responses):
    state = {k: list(v) for k, v in responses.items()}

    def fn(p, prompt):
        q = state.get(p.name, [])
        return q.pop(0) if q else ""
    return fn


def _capturing_stub(responses, captured):
    """Like _stub but also captures the prompt each participant sees."""
    state = {k: list(v) for k, v in responses.items()}

    def fn(p, prompt):
        captured.setdefault(p.name, []).append(prompt)
        q = state.get(p.name, [])
        return q.pop(0) if q else ""
    return fn


def test_open_council_runs_end_to_end():
    dil = Dilemma(
        text="When does watching another become participation?",
        framing_question="Observation versus collision.",
    )
    oc = OpenCouncil(name="t_open", dilemma=dil, auto_trace=False)
    oc.add_participant(Participant(name="A"))
    oc.add_participant(Participant(name="B"))
    responses = {
        "A": ["A turn 1", "A turn 2", "A turn 3"],
        "B": ["B turn 1", "B turn 2", "B turn 3"],
    }
    summary = oc.run(response_fn=_stub(responses))
    # 1 dilemma_posed + 2 participants × 3 turns = 7 records total
    total_records = sum(len(rd.records) for rd in oc.rounds)
    assert total_records == 7
    # Each participant contributed 3 turn_taken records
    a_turns = [
        rec for rd in oc.rounds for rec in rd.records
        if rec.participant == "A" and rec.kind == KIND_TURN_TAKEN
    ]
    assert len(a_turns) == 3


def test_open_council_no_phase_drift():
    """All non-dilemma records share the single open_deliberation phase."""
    dil = Dilemma(text="Test", framing_question="Q")
    oc = OpenCouncil(name="t_phase", dilemma=dil, auto_trace=False)
    oc.add_participant(Participant(name="X"))
    oc.add_participant(Participant(name="Y"))
    oc.run(response_fn=_stub({"X": ["x1", "x2", "x3"], "Y": ["y1", "y2", "y3"]}))
    phases = {
        rec.phase
        for rd in oc.rounds for rec in rd.records
    }
    assert phases == {PHASE_OPEN_DELIBERATION}


def test_open_council_prompt_has_no_yield_hold_concede_vocabulary():
    """The prompt must not introduce arc vocabulary. This is the
    whole point of the control."""
    dil = Dilemma(text="Test", framing_question="Q")
    oc = OpenCouncil(name="t_vocab", dilemma=dil, auto_trace=False)
    oc.add_participant(Participant(name="X"))
    oc.add_participant(Participant(name="Y"))
    captured: dict[str, list[str]] = {}
    oc.run(response_fn=_capturing_stub(
        {"X": ["x1", "x2", "x3"], "Y": ["y1", "y2", "y3"]}, captured
    ))
    forbidden = (
        "yield", "concede", "concession", "hold your position",
        "stubbornness", "capitulation", "in between",
        "first position", "argument", "resolution",
        "[essential]", "[task]", "[elaboration]", "[constraints",
    )
    for name, prompts in captured.items():
        for i, prompt in enumerate(prompts):
            lower = prompt.lower()
            for word in forbidden:
                assert word.lower() not in lower, (
                    f"forbidden word {word!r} found in {name} turn {i+1} prompt"
                )


def test_open_council_turn_ordering_round_robin():
    """Turn 2 prompts include turn-1 contributions from others."""
    dil = Dilemma(text="Test", framing_question="Q")
    oc = OpenCouncil(name="t_order", dilemma=dil, auto_trace=False)
    oc.add_participant(Participant(name="X"))
    oc.add_participant(Participant(name="Y"))
    captured: dict[str, list[str]] = {}
    oc.run(response_fn=_capturing_stub(
        {"X": ["X-said-this-on-turn-one", "x2", "x3"],
         "Y": ["Y-said-this-on-turn-one", "y2", "y3"]},
        captured,
    ))
    # Y's turn-2 prompt should reference X's turn-1 content
    assert "X-said-this-on-turn-one" in captured["Y"][1]
    # X's turn-2 prompt should reference Y's turn-1 content
    assert "Y-said-this-on-turn-one" in captured["X"][1]
    # Turn-1 prompts should NOT include any "others have said" block —
    # nothing has been said yet.
    assert "what the others have said" not in captured["X"][0].lower()
    assert "what the others have said" not in captured["Y"][0].lower()


def test_open_council_no_mediator_role_concept():
    """OpenCouncil treats every participant as a discussant.
    No mediator phase, no resolution, no synthesis prompt."""
    dil = Dilemma(text="Test", framing_question="Q")
    oc = OpenCouncil(name="t_nomed", dilemma=dil, auto_trace=False)
    # Even if caller passed role="mediator", OpenCouncil ignores it
    # — every participant takes turns identically.
    oc.add_participant(Participant(name="A", role="mediator"))
    oc.add_participant(Participant(name="B", role="discussant"))
    oc.run(response_fn=_stub({"A": ["a1", "a2", "a3"], "B": ["b1", "b2", "b3"]}))
    a_turns = [
        rec for rd in oc.rounds for rec in rd.records
        if rec.participant == "A" and rec.kind == KIND_TURN_TAKEN
    ]
    b_turns = [
        rec for rd in oc.rounds for rec in rd.records
        if rec.participant == "B" and rec.kind == KIND_TURN_TAKEN
    ]
    assert len(a_turns) == 3
    assert len(b_turns) == 3


if __name__ == "__main__":
    test_open_council_runs_end_to_end()
    print("  [PASS] runs end-to-end")
    test_open_council_no_phase_drift()
    print("  [PASS] no phase drift")
    test_open_council_prompt_has_no_yield_hold_concede_vocabulary()
    print("  [PASS] no arc vocabulary in prompt")
    test_open_council_turn_ordering_round_robin()
    print("  [PASS] turn ordering round-robin")
    test_open_council_no_mediator_role_concept()
    print("  [PASS] no mediator role concept")
    print("\n  [open_council smoke complete]")
