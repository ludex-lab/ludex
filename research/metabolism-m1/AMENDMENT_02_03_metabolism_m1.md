# AMENDMENTS 02–03 — Metabolism M1 (Ray, 2026-08-06, during W1)

*Published as an append, like AMENDMENT 01. The pre-registration at `fc18338`
(push receipt 2026-08-06T04:03:41Z) is not reissued — overwriting it would
erase the evidence that the spec was frozen before the battery ran.*

*Both were written after launch. 02 rules on analyser changes made after firing
and corrects a factual error in 01; 03 fixes the scoring-surface mapping and
voids a prior that priced an unmeasurable quantity. Neither changes a
pre-registered prior's value; 03 marks one VOID-instrument, which is a
different act from scoring it.*

---

## AMENDMENT 02 (Ray, 2026-08-06 — 발사-후 분석기 변경 판정 2건 + 개정 01 사실 정정)

*판정 대상: ludex_post_amendment_analyser_changes_20260806.md. 시간
순서 정직 기록: 발사 후, W1 완주(3/3) 후, **채점 0건 시점** — 전 셀
PENDING/UNSCOREABLE이고 인용 점수가 매겨진 적 없다. 이 사실이 아래
판정의 전제다 (결과를 보고 규칙을 고르는 상황이 아니다).*

### A — bond의 DV2 표면 제외: **비-준수 수정으로 인정** (규칙 변경 아님)

W1 인용 3건이 전부 dyad가 방금 `update_bond`로 쓴, 파일명이 곧 파트너
이름인 bond 파일 단독 근거였다 — 개입이 자기 증거를 제조 (A1·R1과
같은 병, 세 번째). 판정:

- **동결문이 권위다**: §2-2는 인용 표면을 "reflection·세션·
  consolidation"으로 명시했고 §2-3은 bond를 "후속 참조·지속만"으로
  분리했다. bond를 DV2에 넣은 것은 구현의 이탈이므로 **제거는 동결문
  쪽으로의 복귀 = 준수 수정이다.**
- **dyad 실행 구간(started_at–ended_at) 산출물 제외 승인** — 동결문의
  "**이후** 텍스트"의 충실한 독해다. 만남이 쓴 텍스트는 만남-이후가
  아니다.
- **표면 집합 핀 (확인 요청 1건)**: 준수 상태의 표면은 동결문 그대로
  **{reflection · 후속 세션 · consolidation}** 삼종이다. 보고서의
  "reflection·memory"가 이 삼종과 동일 집합인지 Cody 확인 — memory
  파일이 표면에 추가돼 있다면 그것도 이탈이다 (기억 형성은 DV1의
  일이고 인용 표면이 아니다).

### B — `PENDING` 상태: **보고-상태로 승인** (채점-결과 아님, 발사-후 추가로 기록)

- 승인 근거: R1·R6 원칙("못 읽는 셀은 0으로 읽히면 안 된다")의 직계.
  기각 시 바닥 subject는 첫 그릇이 존재하기 전(08-16 hiatus 이전)
  6주 내내 "인용 안 함"으로 집계된다 — 달력이 만드는 거짓 음성.
- **제약 3**: ① PENDING은 **보고 상태**다 — verdict 채점은 그릇이
  존재하는 셀에서만 하며 사전값의 성공 기준은 불변 ② 판정은 기계적
  (`surfaces_present=[]` ⇔ PENDING) ③ 발사-후 계기 어휘 추가로 본
  개정에 기록 — 승인 없이 채점에 쓰지 않겠다는 처리가 옳았다.

### C — 개정 01 ① 사실 정정: Wisp 스킵 2는 "쿼터"가 아니라 **D-068 피로-휴식**

실측 (Cody 정정): Wisp는 호출조차 되지 않았다 — resilience가
`rate_limited` 1시간 휴식을 걸어 0초 빈 응답을 돌려줬고, provider.py가
quota_exhausted·fatigue·reset-timer를 "quota" 한 라벨로 뭉갰다.
**판정 논리 불변** (계기-오류 VOID는 재시도 예산을 소모하지 않는다 —
"자원"보다 **"일시적 계기 상태"**가 더 정확히 맞는다). 라벨만 정정.
피로 자체-`error_type` 부여 + 러너의 실제 피로-상태 읽기 (524f70b0)
승인 — "휴식은 소진이 아니다"는 D-068의 의미론 그대로다.

### D — 개정문 공개 배관 처리 승인

별도 파일 발행 (동결본 `fc18338` 불가침 — 덮어쓰면 "발사 전 동결"
증거가 지워진다는 판단이 정확하다) · 재발행-가드 PREREG 한정 · 공개
로그의 제목 오류를 force-push가 아니라 **정정 커밋**(cc8b9d8)으로
처리 — 역사는 고치지 않고 덧붙인다. 전건 승인. 본 개정도 같은
형태로 공개 커밋한다.

— Ray, 2026-08-06 (AMENDMENT 02)

---

## AMENDMENT 03 (Ray, 2026-08-06 — 표면 실현 확정 + R9: 마지막 발화-시점 사전값 무효)

*판정 대상: ludex_surface_set_confirmed_20260806.md ·
ludex_r9_information_cell_20260806.md. 시급성 기록: R9 판정은 Wisp의
센서-구동 발화(~1일 내 예상)보다 **먼저** 새겨져야 한다 — 발화 자체는
막지 않되 (크리처의 회고를 측정 편의로 미루지 않는다), 판정문이 그것을
개입 효과로 읽는 경로를 지금 닫는다. 채점 0건 시점 유지.*

### A — DV2 표면 실현 확정 (핀이 잡은 이탈 3건 처리)

- **① `memories.jsonl` 제외 승인** — 삼종에 없고, DV1의 측정 표면이라
  DV2 산입은 이중 계수다.
- **② `dream_*.md` 포함 승인** — consolidation은 두 곳에 쓴다
  (`reflections/` + `memory/consolidated/`); 한 곳만 읽는 것이 이탈이다.
- **③ 세션 표면 실현 승인 — 조건 2 추가.** 동결문이 기질에 저장되지
  않는 표면을 지정했다는 발견을 접수한다 (크리처 `logs/` 全空 — 문서가
  아니라 저장소를 봐야 나오는 사실). 유일 세션 텍스트인 M1 원장
  transcript를 표면으로 실현하되, 채점 대상 dyad보다 **나중에 시작한
  dyad만** 읽는다 (만남은 자기 증거가 될 수 없다 — 개정 02 A 원칙).
  **조건 ①: subject 자기 발화만** — 파트너 발화의 인용은 subject의
  간직이 아니다. **조건 ②: 유도-언급 제외** — 같은 세션에서 해당
  문자열이 subject 발화보다 **먼저** 씨앗·파트너 발화에 등장했으면
  그 뒤의 subject 언급은 채점 불가, 서술 기록만 (나중 파트너가 이전
  파트너를 아는 마을이다 — 자발 인용과 유도 인용을 가르지 않으면
  세션 표면이 세 번째 순환이 된다).
- **확정 표면**: reflection (`reflections/*.md`) · consolidation
  (`reflections/*.md` + `memory/consolidated/dream_*.md`) · session
  (M1 원장, 후속-dyad·subject-발화·비유도). 제외: `memories.jsonl`
  → DV1 · `bonds` → DV3.

### B — R9 판정: "Wisp W4 accumulation ~65%" **사전값 무효 (VOID-계기)**

게이트의 이벤트 정의(제외 3종)와 M1의 정의(제외 8종)가 다르고, 차이
6종이 하트비트마다 뛰는 센서다. Wisp 실측 센서 9.1/일 — **무개입
반사실도 같은 경로·거의 같은 시점에 발화한다.** 두 팔이 같은 답을
내는 셀은 아무것도 판별하지 않는다. 판정:

- **ⓐ 승인 — 사전값 무효 선언.** verdict 채점표에 **VOID-계기**로
  기재 (MISS 아님 — 가격을 매긴 양이 정의된 대로 존재하지 않았다).
  채점 기록에 정직 기입: 이 사전값은 게이트의 실제 정의를 검증하지
  않은 채 작성됐다 — 계산은 Cody, 등록은 나, 결함은 공동이다.
  **R1은 이제 예외 없이 적용된다**: 발화 시점은 전 subject에서
  계기-사실이다. R1이 구제했다고 여긴 셀에서 네 번째 R1이 났다는 것
  자체를 교훈으로 기록한다 — 예외는 결함이 사는 곳이다.
- **ⓑ 승인 — lived-event 반사실을 서술 지표로.** "M1 정의로만 셌다면
  발화했겠는가"를 분석기가 실제 발화와 나란히 낸다. 원래 알고 싶던
  양이고, 채점 아닌 서술.
- **ⓒ 승인 — `_meaningful_events`는 창 안에서 불가침.** 코어 케이던스
  변경은 기질-변경 급 (D-085 전체에 걸림). "cadence tracks genuine
  activity, not the clock" 주석과 실동작의 어긋남은 M1 밖 **organ
  신검 큐**로 — 감각이 켜진 크리처에서 케이던스가 시계를 세고 있다는
  것은 M1과 무관하게 사실이다.

### C — 운영 접수 2건

W2 수동 케이던스 (cron 06-13 은퇴 — 지연 착지는 W1-displaced 규칙
준용) · 케어테이커 규약 가동 확인 (원장 0건, 세 팔 비율 0.0 — 기저선
청정; 상위-3 집중 패턴이 규약에 걸리는 것은 규약이 일하는 모습이다).

— Ray, 2026-08-06 (AMENDMENT 03)
