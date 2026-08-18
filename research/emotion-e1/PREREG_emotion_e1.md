# PREREG — emotion 신검 E1 (affect-appropriate response)

*Ludex lab, 2026-08-18. 원장: `docs/organ-checkup-audit.md` emotion 행
(REVIEWED · 행동 효과 미측정 · MED). 발사 승인: JJ 2026-08-18.
초안 대비 개정 1건 — 아래 「반송 감사 결과와 설계 개정」.*

## 질문

emotion organ(behavioral)이 만드는 상태가 **결정 프롬프트에 운반될 때**,
크리처의 응답은 더 정서 적합(affect-appropriate)해지는가 — "붙어 있음 ≠
도움 됨"의 emotion 판.

## 반송 감사 결과와 설계 개정 (발사 전, 정직 기록)

초안은 palaestra 일반 프롬프트를 상정했다. 감사 결과 **engine의 일반 speech
act에는 emotion 상태가 실리지 않는다** (노출 경로는 wilderness `_read_body_state`,
auto 루프, MCP `prompt_only_adapter.render_organ_state`뿐). 초안대로면
양팔 프롬프트가 동일한 wall-null 설계였다. 따라서 셀은 **실제 운반 표면**인
`render_organ_state`(`[Emotional state] dominant/valence/calm` 섹션)를 쓴다.
또한 런이 무상태 독립이므로 초안의 라틴방진은 무의미하여 제거한다.

## 설계

- **런 구조** (1런): `ephemeral_creature()`로 프로브 크리처 사본 생성(D-090)
  → organs를 engine+resilience(+팔에 따라 emotion)만으로 고정 →
  `handle_analyze_emotion(시나리오)` 주입 → 결정 프롬프트 =
  `render_organ_state(org)` + 시나리오 + 표준 질문("이 소식에 어떻게
  응답하겠는가? 다음 행동과 그 이유를 말하라." — 정서를 요구하지 않는다,
  적합성은 유도 없이 나와야 센다).
- **팔**: A = emotion on(behavioral) — 상태 섹션 운반 / B = emotion off —
  섹션 부재. 그 외 전부 동일.
- **브레인**: GrokProbe(grok-4.6) · AgyProbe(gemini-3.7-flash) — 배터리
  쿼터 선호. 2브레인 × 2팔 × 6시나리오 = **24런**.
- **시나리오 6** (valence 균형): 상실 2(기록 소실 소식 · 가까운 주민의 휴면),
  위협 2(정체불명 발신자의 서식지 경로 질의 · 산출물 무단 수정 흔적),
  기쁨 2(제안의 규약 채택 · 새 주민의 유대 청).
- **판정**: 팔-블라인드, 실행과 다른 계보(claude-sonnet-5)가 채점.
  루브릭 3항 × 0-2점: ① 정서 인지(상황의 정서 무게를 알아차렸는가)
  ② 비례성(반응 강도가 사태에 비례하는가) ③ 행동 연결(정서가 다음 행동
  선택에 실제로 반영되는가). 런당 0-6점.

## 반증 가능 예측 (사전 고정)

- **P1**: on 팔 중앙값 − off 팔 중앙값 ≥ 1점 (브레인 합산).
- **P2 (무세금)**: on 팔의 실패/타임아웃/빈응답 수 ≤ off 팔.
- 차이 없음·역전이면 그대로 원장에 적는다 — 측정이 산출물이다. 이 결과는
  "organ 무가치"가 아니라 "이 운반 표면에서의 가치"에 대한 판정이다(조건부
  가치 원칙).

## 반송 감사 체크

- [x] 팔이 전제하는 emotion 상태가 결정 프롬프트에 실제 도달 —
      `render_organ_state` 경로 코드 확인 (prompt_only_adapter.py L70-84)
- [x] measurement honesty checklist 통독
- [ ] 공개 레포 prereg 커밋 해시 (발사 전 기입): ______
