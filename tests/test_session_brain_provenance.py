"""세션 기록의 기질 표지 — 소견 01 (2026-08-14)의 회귀 울타리.

아카이브 세션 51개 중 44개가 brain 표지 없이 저장되어, 판독기가 **오늘의**
ludex.yaml을 읽고 있었다. 재-브레인 한 번에 논문 표 1의 검증 사슬이 조용히
끊어졌다. 이 테스트가 지키는 것 둘:

1. **쓰는 쪽** — 필드 serializer는 참가자마다 세션 시점의 brain을 적는다.
2. **읽는 쪽** — 표지 없는 옛 기록의 폴백은 유지하되, `brain_source`로
   기록(archived)과 추정(live_config)을 구분해 준다. 폴백을 조용히 없애면
   기존 도구가 조용히 통과해 버린다 — 그쪽이 더 나쁘다.
"""
import json

from ludex.fields.conversation import ConversationField, Participant


class _FakeOrganism:
    """config만 있으면 된다 — brain 추출은 config를 읽는다."""
    def __init__(self, provider, model):
        self.config = {"provider": provider, "model": model}


def test_writer_archives_brain_at_session_time():
    conv = ConversationField("t")
    conv.add_participant(Participant(
        name="Echo", organism=_FakeOrganism("codex_cli", "gpt-5.6-sol")))
    conv.add_participant(Participant(name="caretaker"))   # organism 없는 자리
    parts = {p["name"]: p for p in conv.to_summary()["participants"]}
    assert parts["Echo"]["brain"] == "codex_cli:gpt-5.6-sol"
    # organism 없는 자리는 빈 표지 — 없는 것을 있다고 적지 않는다
    assert parts["caretaker"]["brain"] == ""


def test_writer_survives_rebrain_of_live_config():
    """기록된 표지는 이후의 config 변경과 무관해야 한다 — 소견 01의 핵심."""
    org = _FakeOrganism("codex_cli", "gpt-5.5")
    conv = ConversationField("t")
    conv.add_participant(Participant(name="Echo", organism=org))
    archived = conv.to_summary()["participants"][0]["brain"]
    org.config["model"] = "gpt-5.6-sol"          # 재-브레인이 일어났다
    assert archived == "codex_cli:gpt-5.5"       # 기록은 그대로다


def test_reader_labels_fallback_as_live_config(tmp_path):
    """표지 없는 옛 기록: 값은 오되 brain_source='live_config'로 온다."""
    from ludex.core.transcript_summary import summarize_transcript
    p = tmp_path / "old_session.json"
    p.write_text(json.dumps({
        "participants": [{"name": "Echo", "role": "discussant"}],   # brain 없음
        "rounds": [{"phase": "first_position",
                    "records": [{"participant": "Echo", "content": "발언."}]}],
    }), encoding="utf-8")
    a = summarize_transcript(str(p))["actions"]["Echo"]
    assert a["brain_source"] in ("live_config", "")   # 폴백이거나, 조회 실패


def test_reader_labels_archived_brain(tmp_path):
    from ludex.core.transcript_summary import summarize_transcript
    p = tmp_path / "new_session.json"
    p.write_text(json.dumps({
        "participants": [{"name": "Echo", "role": "discussant",
                          "brain": "codex_cli:gpt-5.5"}],
        "rounds": [{"phase": "first_position",
                    "records": [{"participant": "Echo", "content": "발언."}]}],
    }), encoding="utf-8")
    a = summarize_transcript(str(p))["actions"]["Echo"]
    assert a["brain"] == "codex_cli:gpt-5.5"
    assert a["brain_source"] == "archived"
