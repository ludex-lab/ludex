# AMENDMENT 01 — Metabolism M1 (Ray, 2026-08-06, during W1)

*Published separately, and that is the point. The pre-registration published at
`fc18338` (push receipt 2026-08-06T04:03:41Z) is NOT reissued: overwriting it
would erase the very thing the public commit exists to show, that the spec was
frozen before the battery ran. An amendment is an append with its own date, so
a reader can see what was fixed before firing and what was ruled after — the
same shape as the physics-e1 bundle's CORRECTION_01..03 files.*

*This amendment was written after launch, mid-W1. Nothing in it changes a
pre-registered prior or a scoring rule.*

---

## AMENDMENT 01 (Ray, 2026-08-06 — 발사 후 W1 중, 판정 2건 + 게이트 해석 승인)

*발사 보고 (ludex_m1_launch_and_rebrain_20260806.md) 판정. 시간 순서
정직 기록: 본 개정은 발사(04:15:19Z) 후 작성됐다. 다만 판정 대상은
설계 변경이 아니라 **동결문이 다루지 않은 사례 2건의 사후 처리 규칙**
이며, 어느 판정도 이미 착지한 데이터를 소급 변경하지 않는다. 본
개정도 공개 리포에 커밋된다 (순서 규약의 연장 — Cody 배관).*

### A1 — 우리-오류 VOID의 재발사 의미론 (Wisp×Flare 판정)

W1 Wisp×Flare 스킵 2건: ① 러너 `.env` 미로드 (키 미전달 —
**계기-오류 VOID**) ② Wisp codex 쿼터 소진 (**자원**). 판정:

- **계기-오류 VOID는 dyad의 재시도 예산을 소모하지 않는다** — §4의
  "재시도 1회"는 크리처/브레인 실패를 상정한 규칙이다. E1 A3 선례
  그대로: 계기 실패가 데이터로 위장하지 않게 하는 것이 VOID의 존재
  이유이고, 그 대칭으로 **계기 실패가 크리처의 기회를 소모해서도 안
  된다.**
- **재발사 승인**: Wisp 쿼터 창 회복 즉시. W1 밖에 착지하면
  `W1-displaced` 라벨 — 주차 산정은 원 배정(W1) 기준, 시각은 실제
  기준 (귀속은 어차피 dyad 자체 경계다, §4-1).
- 원장 처리 승인: 스킵 2건 원문 유지 + 진단 덧붙임 (고쳐 쓰지 않음).
- 러너 수정 2건 (사유 분류 되읽기 · 재시도 backoff) 승인 — 계기-레인.

### A2 — 창-안 의도된 기질 변경의 재-스탬프 처리 (파트너 리브레인 3)

JJ 결정으로 파트너 3 리브레인 (Comet: auth만 P축 · Flare/Spark:
gemini_cli→agy A축, 모델 보존, effort=medium 계약 강제). 판정:

- **"스팬이 뒤에 있는 의도된 변경"으로 기록한다** — 조용한 드리프트가
  아니다. 이중 스탬프 보존 (`m1_gate_launch_preRebrain.json` +
  `m1_gate_launch.json`), **후자가 창의 작동 기준선**이다.
- **창의 기질-균일성은 보존됐다**: 변경 전 api-파트너가 데이터를 만든
  dyad = 0건 (완주 2건은 codex·agy-구독) — 전 16셀이 변경-후 기질에서
  돈다. 이 타이밍 판단(지금이 가장 깨끗한 시점)을 승인한다.
- verdict 재-스탬프는 **후-스탬프와 대조**한다. **창 안 추가 파트너
  기질 변경은 이제부터 격리-급이다** — 이 개정이 그 문을 닫는다.
- D-086 의례 (스냅샷→스모크→스팬→서사) 이행 확인. 부수 관측 (기질-형제
  목소리 수렴, D-044 연속성) 접수 — 별도 관측 파일 후보.

### A3 — 게이트 해석 승인 (M1은 밀실이 아니다)

canary_gate의 ACT fail-closed는 잠긴-방 프로브의 의미론이다. M1
크리처는 자기 하비타트에서 대화하고 bond를 쓰는 것이 정상이므로
**생존(alive)만 차단 조건, act·leak은 기록 후 판정 저울**로 — Cody
해석 승인. `allow_denied_tool_act` 플래그를 독스트링의 금지("빨간불을
초록으로")대로 **쓰지 않은 것**이 옳았다 — 플래그 우회 대신 게이트
의미론을 워크 성격에 맞게 재선언하고 그 재선언을 기록하는 것, 이게
E2 §5가 세운 규율의 정확한 적용이다.

— Ray, 2026-08-06 (AMENDMENT 01)
