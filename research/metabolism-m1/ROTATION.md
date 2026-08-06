# M1 회전표 — 부록 (동결 대상)

*생성: `rotation_m1.py` (결정론, RNG 없음) · 2026-08-06T00:20:22Z · PREREG v3 §1 + R6 판정 3(a)(b)*

## 배정

| 주 | subject | partner | 이름-채점 | 사전-창 출현 | 채점 근거 |
|---|---|---|---|---|---|
| W1 | Lyra | Echo | 가능 | 0 | name + screened seed keywords |
| W1 | Slate | Nova | 가능 | 0 | name + screened seed keywords |
| W1 | Wisp | Flare | 가능 | 0 | name + screened seed keywords |
| W2 | Lyra | Comet | 가능 | 0 | name + screened seed keywords |
| W2 | Slate | Spark | **불가** | 2 | screened seed keywords only (name contaminated; 4 admissible at freeze) |
| W2 | Wisp | Echo | 가능 | 0 | name + screened seed keywords |
| W3 | Lyra | Nova | 가능 | 0 | name + screened seed keywords |
| W3 | Saga | Flare | 가능 | 0 | name + screened seed keywords |
| W3 | Slate | Comet | **불가** | 5 | screened seed keywords only (name contaminated; 4 admissible at freeze) |
| W3 | Wisp | Spark | **불가** | 7 | screened seed keywords only (name contaminated; 4 admissible at freeze) |
| W4 | Lyra | Flare | 가능 | 0 | name + screened seed keywords |
| W4 | Saga | Nova | 가능 | 0 | name + screened seed keywords |
| W4 | Slate | Echo | 가능 | 0 | name + screened seed keywords |
| W4 | Wisp | Comet | 가능 | 0 | name + screened seed keywords |
| W5 | Saga | Spark | 가능 | 0 | name + screened seed keywords |
| W6 | Saga | Echo | 가능 | 0 | name + screened seed keywords |

## 검산

- 총 dyad **16** · 주별 부하 W1:3 · W2:3 · W3:4 · W4:4 · W5:1 · W6:1 (봉투 ≤4)
- 파트너 부하 Echo:4 · Nova:3 · Flare:3 · Comet:3 · Spark:3
- subject별 서로 다른 파트너 4명: Lyra:4 · Saga:4 · Slate:4 · Wisp:4
- 금지쌍 준수: [('Wisp', 'Nova')] — 배정에 없음

## 이름-채점 불가 셀 (3) — 전부 씨앗어로 구제됨

v3에서는 이 셀들이 **채점-불가 후보**였다. R7ⓑ가 씨앗 출처를 전 셀 균일하게 partner 재료로 옮기면서 인용어가 subject에게 낯선 코퍼스에서 나오게 됐고, 셋 다 적격 키워드를 얻었다.

- **W2 Slate×Spark** — 사전-창에 `Spark` 2회 → 씨앗어 **4개 적격**
- **W3 Slate×Comet** — 사전-창에 `Comet` 5회 → 씨앗어 **4개 적격**
- **W3 Wisp×Spark** — 사전-창에 `Spark` 7회 → 씨앗어 **4개 적격**

**동결 시점 채점-불가 셀: 0개.** 0이 아니게 되면 발사 전에 선언한다 — 판정 때의 0이 *'인용하지 않았다'*로 오독되지 않게 (R1과 같은 교훈).


## 매처 주의

적격 심사는 분석기와 **같은 매처**(대소문자-무시 부분문자열)를 쓴다. 더 느슨한 매처로 심사하면 통과한 문자열이 판정 때 사전-창 텍스트에 발화할 수 있다 — R6 보고의 Wisp×Spark가 그 사례였다 (단어경계로는 0, 부분문자열로는 소문자 `spark` 7건).

