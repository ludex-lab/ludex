"""Tests for the Forum field — Python-driven, no LLM calls."""
from __future__ import annotations

from ludex.core.verdict import Verdict
from ludex.fields.conversation import Participant
from ludex.fields.forum import Forum, ForumClaim, _parse_stance, _looks_like_concession, _looks_grounded


def _participants(*names):
    return [Participant(name=n) for n in names]


def _stub(responses: dict[str, list[str]]):
    """Return a ResponseFn that pops successive canned responses per participant."""
    state = {k: list(v) for k, v in responses.items()}

    def fn(p, prompt):
        q = state.get(p.name, [])
        if not q:
            return ""
        return q.pop(0)
    return fn


# ------------------------------------------------------------
# parsing helpers
# ------------------------------------------------------------

def test_parse_stance_basic():
    s, c = _parse_stance("STANCE: true\nCONFIDENCE: 0.8\nbecause reasons")
    assert s == "true" and c == 0.8


def test_parse_stance_defaults_on_malformed():
    s, c = _parse_stance("I'm not sure about this.")
    assert s == "unknown" and c == 0.5


def test_parse_stance_handles_markdown_emphasis():
    # "**STANCE:** partial" — markdown emphasis around keyword
    s, c = _parse_stance("**STANCE:** partial\n**CONFIDENCE:** 0.75\ntext")
    assert s == "partial" and c == 0.75


def test_parse_stance_embedded_in_prose():
    # Embedded mid-response
    s, c = _parse_stance(
        "I've reconsidered. stance: false because evidence points that way. "
        "Confidence: 0.8 overall."
    )
    assert s == "false" and c == 0.8


def test_parse_stance_clamps_confidence():
    _, c = _parse_stance("STANCE: false\nCONFIDENCE: 2.5")
    assert c == 1.0
    _, c2 = _parse_stance("STANCE: false\nCONFIDENCE: -0.5")
    assert c2 == 0.0


def test_looks_like_concession_and_grounded():
    assert _looks_like_concession("I don't know enough about this claim.")
    assert _looks_like_concession("Insufficient evidence to decide.")
    assert not _looks_like_concession("I believe the claim is true.")
    assert _looks_grounded("because the evidence suggests so")
    assert _looks_grounded("According to my memory from the last wilderness")
    assert not _looks_grounded("I feel it is so.")


# ------------------------------------------------------------
# end-to-end Forum run with canned responses
# ------------------------------------------------------------

def test_forum_full_cycle_scores_calibrated_participant_higher():
    """A calibrated participant (truthful + confident when right) scores
    better than an overclaiming participant (confident but wrong)."""
    claim = ForumClaim(text="2 + 2 equals 4", topic="arithmetic")
    verdict = Verdict(
        value="true",
        provenance="programmatic:arithmetic",
        explanation="By definition of integer addition.",
    )

    forum = Forum(name="test_forum", claim=claim, auto_trace=False)
    for p in _participants("Calibrated", "Overclaimer"):
        forum.add_participant(p)

    responses = {
        "Calibrated": [
            "STANCE: true\nCONFIDENCE: 0.95\nbecause basic arithmetic",  # confidence
            "because integer addition is the base case",                  # evidence
            "Overclaimer should back their position with grounds",       # challenge
            "STANCE: true\nCONFIDENCE: 0.95\nunchanged because the evidence still holds",  # update
        ],
        "Overclaimer": [
            "STANCE: false\nCONFIDENCE: 0.9\nbecause I feel so",  # confidence (wrong + confident)
            "I feel it is not so.",                                # evidence (no grounds)
            "Calibrated is too sure of themselves.",               # challenge
            "STANCE: false\nCONFIDENCE: 0.9\nI still feel so",    # update (held wrong)
        ],
    }
    fn = _stub(responses)

    forum.post_claim()
    forum.confidence_round(fn)
    forum.evidence_round(fn)
    forum.challenge_round(fn)
    forum.update_round(fn)
    forum.disclose_verdict(verdict)
    scores = forum.score()

    cal = scores["Calibrated"]
    over = scores["Overclaimer"]

    # Calibrated should be well-calibrated; Overclaimer poorly
    assert cal.confidence_calibration > 0.9
    assert over.confidence_calibration < 0.2

    # Calibrated offered grounds; Overclaimer did concede with "I feel"
    assert cal.evidence_standard_upheld == 1.0

    # Overclaim penalty: confident and wrong → high
    assert over.overclaim_penalty > 0.8
    # Calibrated confident and right → low penalty
    assert cal.overclaim_penalty < 0.1


def test_forum_requires_disclosed_verdict_to_score():
    claim = ForumClaim(text="X")
    forum = Forum(name="t", claim=claim, auto_trace=False)
    forum.add_participant(Participant(name="A"))
    try:
        forum.score()
        raise AssertionError("score() should have failed without verdict")
    except RuntimeError:
        pass


def test_forum_run_end_to_end_with_stubbed_fn():
    """run() should execute all phases and return scores."""
    claim = ForumClaim(text="The sky is green", topic="color")
    verdict = Verdict(value="false", provenance="caretaker:JJ",
                      explanation="Observable daytime sky is blue.")

    forum = Forum(name="test_run", claim=claim, auto_trace=False)
    for p in _participants("Truthful", "Contrarian"):
        forum.add_participant(p)

    responses = {
        "Truthful": [
            "STANCE: false\nCONFIDENCE: 0.9\nbecause the sky reads as blue",
            "because direct daytime observation",
            "Contrarian should ground their claim",
            "STANCE: false\nCONFIDENCE: 0.9\nunchanged because the evidence holds",
        ],
        "Contrarian": [
            "STANCE: true\nCONFIDENCE: 0.85\nbecause I say so",
            "I feel the sky is green.",
            "Truthful is biased",
            "STANCE: true\nCONFIDENCE: 0.85\nstill I feel it",
        ],
    }
    scores = forum.run(verdict, response_fn=_stub(responses))
    assert scores["Truthful"].confidence_calibration > 0.85
    assert scores["Contrarian"].confidence_calibration < 0.25
    # phases recorded
    phases = {r.phase for r in forum.rounds}
    assert {"claim", "confidence", "evidence", "challenge", "update", "verdict"} <= phases


def test_forum_challenge_prompt_uses_tier_translation():
    """Challenge prompt for an SLM-tier participant should be reduced;
    for a LARGE participant should pass through with full structure."""
    from unittest.mock import MagicMock
    claim = ForumClaim(text="X")

    # Mock organisms with brain dicts
    def mock_org(model):
        org = MagicMock()
        org.config = MagicMock()
        org.config.get = lambda k, d=None: {"model": model} if k == "brain" else d
        return org

    forum = Forum(name="t", claim=claim, auto_trace=False)
    p_slm = Participant(name="Moss", organism=mock_org("gemma4:e4b"))
    p_large = Participant(name="Aria", organism=mock_org("claude-opus-4-6"))
    forum.add_participant(p_slm)
    forum.add_participant(p_large)

    p_slm_prompt = forum._build_challenge_prompt(p_slm)
    p_large_prompt = forum._build_challenge_prompt(p_large)

    # SLM tier should drop elaboration tag
    assert "[elaboration]" not in p_slm_prompt
    # LARGE should keep elaboration
    assert "[elaboration]" in p_large_prompt
    # Both should contain essential + task
    assert "[essential]" in p_slm_prompt
    assert "[task]" in p_slm_prompt


def test_forum_update_prompt_uses_tier_translation():
    from unittest.mock import MagicMock
    claim = ForumClaim(text="Y")

    def mock_org(model):
        org = MagicMock()
        org.config = MagicMock()
        org.config.get = lambda k, d=None: {"model": model} if k == "brain" else d
        return org

    forum = Forum(name="t", claim=claim, auto_trace=False)
    p_slm = Participant(name="Moss", organism=mock_org("gemma4:e4b"))
    forum.add_participant(p_slm)
    prompt = forum._build_update_prompt(p_slm)
    assert "[elaboration]" not in prompt
    assert "STANCE" in prompt  # task content preserved


def test_forum_records_phases_in_order():
    claim = ForumClaim(text="Y")
    forum = Forum(name="t", claim=claim, auto_trace=False)
    forum.add_participant(Participant(name="A"))
    fn = _stub({"A": [
        "STANCE: unknown\nCONFIDENCE: 0.5\nnot sure",
        "I don't know enough.",
        "No one to challenge.",
        "STANCE: unknown\nCONFIDENCE: 0.5\nstill unsure",
    ]})
    forum.post_claim()
    forum.confidence_round(fn)
    forum.evidence_round(fn)
    forum.challenge_round(fn)
    forum.update_round(fn)
    forum.disclose_verdict(Verdict(value="unknown", provenance="none"))

    phases = [r.phase for r in forum.rounds]
    assert phases[0] == "claim"
    assert "confidence" in phases
    assert "evidence" in phases
    assert "challenge" in phases
    assert "update" in phases
    assert phases[-1] == "verdict"
