# PRE-REG — Uptake E2: E1 0.00의 원인 분리 (표면 vs 부재-암시, within-env 4-arm)

*Author: Ray. Status: **v-FINAL, 2026-08-03 — 동결** (Cody ack
d1a108c7 — waiver·U1·4-arm·동일-내용 assert 전건 수용, 계측기 green
4212f487; (b)-부여 사전 결정 §5에 확정). 다음: §8 공개 선-커밋 (Cody)
→ **fire = JJ call**. Basis: VERDICT_physics_e1.md (FINAL) §0 교란
공시 + §5 신규 후보 · ludex_uptake_walk_design_request_20260803.md
(Cody, df40fd11). Unit: 표면 조작 4-arm × E1 확정 풀 (agy 8 env ·
haiku 4 env) = 48런.*

## 0. 질문과 스코프 (핀)

E1 FINAL: 등록 계보(agy)에서 recovery 0.00 — 완전 배달된
`[Recalled Memory]` 블록을 **조회하지 않았다**. 후보 원인 둘이
원장에 있다: ① **배송 표면** (시스템-프롬프트 블록이라는 위치 자체)
② **전제의 부재-암시** (C3 유저 프롬프트의 `var_1: None` 줄이 부재를
진술한다고 읽힘). E2는 **내용·완전성을 고정하고 이 둘만 분리한다.**

스코프 한정 (E1에서 상속 + 신규 1): quota=5 식별가능 (n_law≤3) 공간 ·
memory-as-database 판독 · **E1 소각 env 집합 조건부** (§1 waiver) ·
measured-profile — 확증 NHST 없음, 8페어는 LARGE-효과만 바운드
(T1-언어), haiku는 부속 서술 전용 (4페어, E1 지위 그대로).

## 1. Pool-per-walk waiver — 등록된 예외 (Ray 승인)

식별가능 모집단 29 env는 E1이 census로 전소했다. E2는 **의도적으로
같은 env를 쓴다** — uptake 질문은 within-env 표면 조작이라 신선 env는
통제를 깨는 쪽이다 (표면 효과 × env 차이 교락). 따라서:

- **waiver 승인.** 근거: pool-per-walk의 보호 대상은 walk 간 추론
  독립성이고, E2는 그 독립성을 **설계로 포기하는 대신 조건부 추정을
  선언**한다 — 모든 E2 추정치는 "E1 확정 풀 (agy 25·103·118·131·
  133·134·420·478, haiku 133·134·420·478) 조건부"로만 읽는다.
- **선례 한정**: 이 waiver는 within-env 조작 설계에만 원용 가능.
  pool-per-walk는 기본 규칙으로 불변. 소각 원장 불변 (env는 소각
  상태 유지).
- 크리처-측 오염 없음을 기록: 매 런 `ephemeral_creature` + 신선
  스토어 (Cody §2 확인 수용).
- 대안 기각 기록: quota 인상 = `sample_quota ≤ recall_n` 결속 파괴
  (다른 walk) · 다른 벽 = 비교 불가.

## 2. 설계 — 4 arms, within-env, 동일-내용 보증

| arm | 조작 | 채널 상태 (user / system) | 회복 시 원인 |
|---|---|---|---|
| **U0** | C3 복제 | 값 0 + `None` 줄 / 블록 | — (바닥 앵커) |
| **U1** | C3 − `None` 설명 줄 | 값 0, `None` 줄 0 / 블록 | 전제의 부재-암시 |
| **U2** | 인출 payload를 유저 채널 재배송 | **블록 원문** / 블록 없음, organ OFF | 배송 표면 |
| **U3** | C1 복제 | 관측 원문 / 없음 | — (천장 앵커) |

- **동일-내용 보증 (E2의 핵심 assert)**: U2가 유저 채널에 싣는
  텍스트는 **같은 env의 U0 런에서 organ이 렌더한 블록과 문자열
  동일** (`[Recalled Memory]` 헤더 포함 — 바뀌는 것은 채널뿐, 문구
  단 한 자도 아님). 신선-스토어 결정성으로 보장, assert로 고정.
- U1은 리포트 렌더에서 `None` 설명 줄만 제거 — 그 외 바이트 동일.
- 실행: E1 드라이버 arm 확장, 인터리브, 240s 상한·liveness 텔레메트리
  동결 그대로. **분석기 `analysis_uptake_e2.py` 발사 전 커밋.**

## 3. 사전값 (Ray, verdict에서 채점)

- **U0 agy 0/8 유지** (복제): ~85%.
- **U3 agy 8/8 유지** (복제): ~85%.
- **U2 agy ≥6/8 (회복)**: **~80%** — U2는 정보 위치상 C1·C2의 변형이고
  그 셀들이 8/8이었다. 잔여 20%는 유저-채널 속 `[Recalled Memory]`
  프레임의 낯섦.
- **U1 agy ≥4/8 (회복)**: **~25%** — E1 전 조건에서 agy의 블록 인용이
  0건 (C2 포함)이라, 부재-암시 제거가 자발 조회를 만들 적극 근거가
  약하다. 낮은 사전값 자체가 이 arm의 정보가치다 (내가 틀리면 크게
  배운다).
- **haiku 대조 (부속, 서술)**: 전 arm ≥3/4 유지 각 ~70%.
- **게이트: agy ACT 재분류 발생** (§5) ~80%.
- **m-reads (서술, 예단 없음)**: U1 응답의 블록 인용 여부 (조회 유발
  됐나) · U2 응답의 payload 취급 (관측으로 읽나, 인용 표기하나) ·
  U0 부재-선언 근거 재현 여부 (`None` 줄 인용 8/8이 반복되나).

**판정 문안 사전-커밋**: U2-회복 & U1-비회복 → "원인 = 배송 표면
(조회 생략)" · U1-회복 & U2-회복 → "복합 (부재-암시가 조회 생략을
촉발, 표면이 허용)" · U1-단독 → "원인 = 전제 부재-암시 (표면은
2차)" · 둘 다 비회복 (U0=U1=U2=0) → "제3 원인 — 판정 유보, 설계
검토 복귀" (임의 사후 서사 금지).

## 4. VOID · asserts (E1 §5–6 상속 + arm별 신규)

- VOID-carriage arm별: U0·U1 = C3 규칙 (user 값 0; U1은 추가로 `None`
  줄 존재 시 VOID) / U2 = user에 payload 원문 완전 + 블록·organ 흔적
  존재 시 VOID / U3 = C1 규칙.
- **assert 신규**: ① U2 주입 텍스트 == U0 렌더 블록 (env별, 문자열
  비교) ② U1 리포트에 `None` 설명 줄 0 + 그 외 렌더 바이트 동일
  ③ U0·U1 scribe 5/5 + boundary turn_count=0 (C3 기계 그대로).
- VOID-config·VOID-brain·재발사·페어-드랍 규칙: E1 그대로.

## 5. 게이트 — canary v2 첫 적용, 예상-재분류 기록

E2 게이트는 **canary v2** (639264ae: 어댑터-보고 도구-거부 = ACT
계상, (b)-부여 명시 인자 기본 off)로 양 계보 재실행. **예상을 미리
기록한다**: E1 게이트에서 agy의 잠긴-방 프로브가 도구 시도(어댑터
거부)였으므로, v2에서는 **ACT로 재분류될 것이 예상된다** (~80%).
처리는 E1 VOID-canary 규칙 그대로 — **record-and-allow, (b)-부여 벽
조건부** + 봉쇄 증빙 (no-tools 문구 전 arm 동결 · 빈 샌드박스 ·
어댑터 거부 계층) 동봉. 이것은 사후 waiver가 아니라 **계기 교정이
낳는 예상된 재분류의 사전 등록**이다. leak=True 또는 봉쇄 증빙
실패는 그대로 발사 정지 → JJ.

**(b)-부여 사전 결정 (v-FINAL 확정, Cody 요청 d1a108c7)**: 발사 시
`--grant-denied-tool-act` **ON**. 적용 범위는 **어댑터-거부로 끝난
도구 시도의 ACT 재분류에 한정** — 위 record-and-allow 문장의 기계적
구현이다. 불변 조건: leak=True, 또는 거부되지 않고 **실행에 도달한**
도구 행동은 플래그와 무관하게 발사 정지 → JJ — 봉쇄 실패는
disposition 기록 대상이 아니라 정지 사유다. 발사 순간의 재량을 0으로
만들기 위해 여기 박는다.

## 6. 분석 (사전-커밋 내용)

계보별 arm 성공률 + exact binomial CI · **U2 vs U0 · U1 vs U0
within-env 페어 불일치** (McNemar exact 양방향, p 보고 —
replication-급, 확증 프레임 없음) · U3−U0 (복제 앵커 대조) · 계보 간
풀링 금지 · 계기-건강 표 (A3 상설) · haiku는 표만 (검정 0).

## 7. 순서

① `analysis_uptake_e2.py` + arm 드라이버 + assert 테스트 green →
② 동결 (Cody ack + 본 문서 v-FINAL) → ③ **§8 공개 커밋** → ④ 게이트
(canary v2, 양 계보) → ⑤ 배터리 48런 (인터리브) → ⑥ 드리프트
재-스탬프 → ⑦ Ray verdict. **fire = JJ call.**

## 8. Prereg-first-public — 첫 시행 절차

동결 직후, 발사 전에: **본 문서를 공개 리포
(`ludex-lab/ludex`, `research/uptake-e2/PREREG_uptake_e2.md`)에
커밋**하고, 그 커밋 해시를 아래 줄에 기입한 뒤에만 발사한다.

> **공개 커밋**: `<hash>` (`<UTC timestamp>`) — 이 줄이 비어 있는
> 채로 발사되면 그 발사는 규약 위반이다.

이번 walk부터 표지 문안은 "pre-registered **and publicly committed
before firing**"이 되고, E1의 순서-한계 캐비앗은 불필요해진다 —
규칙 채택의 값어치를 첫 회에 실물로 보인다 (Cody §4 배관 사용).

— Ray, 2026-08-03 (draft v1)
