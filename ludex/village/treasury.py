"""Village treasury — the token budget ledger (2026-08-12, JJ's frame:
"인간 나라는 돈으로 예산을 세우지만, AI 세계에서는 토큰양으로 예산을
세운다").

Tokens are the unit in which brain attention flows, so the village
accounts for them the way a town accounts for money. Design:

- The VILLAGE fiscal week starts Monday 00:00 local — provider reset
  windows are opaque and heterogeneous, so the village declares its own
  accounting period rather than guessing at theirs.
- Usage is aggregated from brain_call spans, which already carry
  tokens_in/tokens_out with token_source="estimated" (adapter len/4).
  The ledger says "추정" everywhere it matters: trends and allocation,
  not billing-grade precision.
- Targets are asymmetric: scarce subscriptions get CEILINGS (warn when
  near), abundant ones get FLOORS ("예산을 채우도록" — an unfilled
  floor is the fiscal signal of invitation starvation, answered with
  quality invitations, never busywork). Week one is baseline-only:
  instrument first, policy after data (targets stay None).
- M1-protected work sits OUTSIDE budget logic — the covenant is never
  gated by the ledger. The D-068 fatigue detector remains the hard
  backstop regardless of what this ledger says.

Usage:
    python -m ludex.village.treasury [--habitat Mac-habitat]
    from ludex.village.treasury import write_week_ledger
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from ludex.village.bus import REPO_ROOT, creature_dirs, _iter_spans

# provider → {"ceiling": est-tokens|None, "floor": est-tokens|None}
#
# 2026-08-14: 첫 실수(實數)가 들어왔다. %법으로는 못 잰다는 것이 결론이었고
# (26건/92k 추정 토큰에도 codex 표시가 90→89에서 멈춰 있었다), 실수는 다른
# 문에서 왔다 — 설립자의 대시보드 판독: 본인 사용량 **하루 ~200M 토큰**을
# 7일 지속해도 한도에 닿지 않는다. 그러므로 주간 풀 ≥1.4B, 마을 몫(50%)
# 700M/주 ≈ 100M/일. 우리는 그 0.1% 언저리를 쓴다.
#
# 그래서 codex에는 **바닥만** 건다. 이 부족에서 예산 초과는 위험이 아니고,
# 위험은 배정을 놀리는 것이다. 바닥은 주간 값이며, **우리 추정 토큰 단위**다
# (어댑터 len/4 — 세션이 스스로 끌어온 것은 안 잡히므로 실제보다 작다).
# 바닥을 잡일로 채우지 않는다: 미달은 "더 부르라"가 아니라 "일감이 없다"는
# 신호로 읽고 일의 질과 병렬성으로 답한다.
TARGETS: dict[str, dict] = {
    # 100M/일 × 7 = 700M/주가 배정. 바닥은 배정의 1%로 시작한다 — 지금
    # 우리는 그 1%에도 한참 못 미치므로, 닿을 수 있는 첫 계단부터 건다.
    "codex_cli": {"ceiling": None, "floor": 7_000_000},
    # agy: floor 부족 확정 (2026-08-15). Pro 10TB, 100% 잔여 — 5건·9,885
    # 토큰에도 정수 눈금 불변. codex와 같이 우리 규모에선 천장이 닿지 않으니
    # 바닥만 건다. JJ: "gemini 구독은 quota 못 채울 만큼 풍부, 마구 써도 됨."
    "agy_cli": {"ceiling": None, "floor": 7_000_000},
    # grok: build 버킷 0%에서 측정 중 (2026-08-15). build/imagine 분리 —
    # imagine은 JJ 것. build가 fresh 0%라 첫 1%가 닿을 수 있는 유일한 부족.
    # 계수가 나오면 floor를 그 위에 다시 세운다. 잠정 floor.
    "grok_cli": {"ceiling": None, "floor": 7_000_000},
}

LEDGER_DIR = REPO_ROOT / "village" / "treasury"


def fiscal_week(now: float | None = None) -> tuple[float, str]:
    """Monday 00:00 local of the current village fiscal week."""
    now = now if now is not None else time.time()
    lt = time.localtime(now)
    monday = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - lt.tm_wday,
                          0, 0, 0, 0, 0, -1))
    label = time.strftime("%G-W%V", time.localtime(monday))
    return monday, label


def week_from_label(label: str) -> float:
    """Monday 00:00 local of a named fiscal week ('2026-W34').

    공방 벤치(Ember, treasury_tally)에서 옮겨 온 기능. 본선은 「이번 주」밖에
    셀 수 없었고, 지난 주를 물으려면 이미 쓰인 대장 파일을 읽는 수밖에 없었다.
    """
    monday = datetime.strptime(f"{label}-1", "%G-W%V-%u")
    return time.mktime((monday.year, monday.month, monday.day,
                        0, 0, 0, 0, 0, -1))


def collect(habitat: str = "", now: float | None = None,
            week: str = "") -> dict:
    if week:
        start, label = week_from_label(week), week
    else:
        start, label = fiscal_week(now)
    end = start + 7 * 86400
    by_provider: dict[str, dict] = {}
    by_creature: dict[str, dict] = {}
    for cdir in creature_dirs(REPO_ROOT / "creatures", habitat):
        for span in _iter_spans(cdir):
            if span.get("kind") != "brain_call":
                continue
            # 창에는 상한이 있어야 한다. 상한 없이 세면 「이번 주」에서는 티가
            # 안 나지만 지난 주를 물으면 그 뒤 전부가 딸려 오고, 미래 시각의
            # 스팬도 이번 주로 들어온다. 벤치는 두 경계를 다 썼고 본선은 아래
            # 경계만 썼다 — 이번 주 같은 종류의 세 번째다(M1 감사기·언약 줄).
            ts = span.get("timestamp", 0)
            if ts < start or ts >= end:
                continue
            a = span.get("attributes") or {}
            prov = a.get("provider", "?")
            # 실측과 추정을 한 칸에 더하면 안 된다 — 공방 벤치가 이 구분을
            # 먼저 세웠고, 본선은 세지 않고 있었다.
            src = "measured" if a.get("token_source") == "measured" else "estimated"
            err = a.get("outcome") == "error"
            for bucket, key in ((by_provider, prov), (by_creature, cdir.name)):
                row = bucket.setdefault(key, {
                    "calls": 0, "tok_in": 0, "tok_out": 0, "provider": prov,
                    "out_measured": 0, "out_estimated": 0, "err": 0})
                row["calls"] += 1
                row["tok_in"] += int(a.get("tokens_in") or 0)
                row["tok_out"] += int(a.get("tokens_out") or 0)
                row[f"out_{src}"] += int(a.get("tokens_out") or 0)
                row["err"] += 1 if err else 0
    return {"week_start": start, "label": label,
            "providers": by_provider, "creatures": by_creature}


def _err_cell(row: dict) -> str:
    """실패한 호출의 비율. 공방 벤치(Ember)에서 옮겨 왔다 — 본선 대장은
    토큰만 세고 그 토큰 중 몇 건이 실패였는지는 세지 않았다."""
    calls = row["calls"]
    return f"{row['err']}/{calls} ({row['err'] / calls * 100:.1f}%)" if calls else "—"


def write_week_ledger(habitat: str = "Mac-habitat", week: str = "") -> str:
    d = collect(habitat, week=week)
    label = d["label"]
    lines = [
        f"# 마을 재정 대장 — {label} (회계 주간: 월요일 리셋)",
        "",
        "*단위: **추정 토큰** (brain_call 스팬의 tokens_in/out,",
        "token_source=estimated — 어댑터의 길이 기반 추정). 청구서가 아니라",
        "추세와 배분의 계기다. M1 보호 작업은 예산 논리의 바깥이며, D-068",
        "피로 감지가 최후 방어선으로 별도 작동한다.*",
        "",
        "## 부족(공급자)별",
        "",
        "| 공급자 | 호출 | 추정 토큰 in | 추정 토큰 out | 오류율 | 목표 |",
        "|---|---|---|---|---|---|",
    ]
    total_calls = total_out = 0
    total_measured = total_estimated = 0
    for prov, r in sorted(d["providers"].items(), key=lambda x: -x[1]["tok_out"]):
        t = TARGETS.get(prov) or {}
        tgt = ("천장 " + format(t["ceiling"], ",") if t.get("ceiling")
               else "바닥 " + format(t["floor"], ",") if t.get("floor")
               else "측정 주간 (미설정)")
        lines.append(f"| {prov} | {r['calls']} | {r['tok_in']:,} | "
                     f"{r['tok_out']:,} | {_err_cell(r)} | {tgt} |")
        total_calls += r["calls"]
        total_out += r["tok_out"]
        total_measured += r.get("out_measured", 0)
        total_estimated += r.get("out_estimated", 0)
    lines += [
        "",
        "## 주민별",
        "",
        "| 주민 | 공급자 | 호출 | 추정 토큰 in | 추정 토큰 out | 오류율 |",
        "|---|---|---|---|---|---|",
    ]
    for name, r in sorted(d["creatures"].items(), key=lambda x: -x[1]["tok_out"]):
        lines.append(f"| {name} | {r['provider']} | {r['calls']} | "
                     f"{r['tok_in']:,} | {r['tok_out']:,} | {_err_cell(r)} |")
    lines += [
        "",
        f"*생성: 케어테이커 기계, {time.strftime('%Y-%m-%d %H:%M')}. 매일 갱신,",
        "월요일에 지난 주간 대장이 게시판 공보로 요약된다.*",
        "",
    ]
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    out = LEDGER_DIR / f"ledger-{label}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    provs = " · ".join(f"{p} {r['calls']}건/{r['tok_out']:,}tok"
                       for p, r in sorted(d["providers"].items(),
                                          key=lambda x: -x[1]["tok_out"]))
    # 합계 한 칸은 63%가 추정인 것을 실측처럼 읽히게 한다 (08-27 실측).
    split = f"실측 {total_measured:,} + 추정 {total_estimated:,}"
    return (f"treasury {label}: {total_calls}건, out {total_out:,}tok "
            f"({split}) — {provs}")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Write the village token-budget ledger.")
    ap.add_argument("--habitat", default="Mac-habitat")
    ap.add_argument("--week", default="", metavar="YYYY-Www",
                    help="집계할 회계 주간 (기본: 이번 주)")
    args = ap.parse_args()
    print(write_week_ledger(args.habitat, args.week))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ── 계기의 한계 (2026-08-14 관측) ────────────────────────────────────────
# 우리의 추정 토큰은 **우리가 보낸 프롬프트**의 길이에서 나온다. 세션이
# 스스로 끌어온 것 — 웹에서 가져온 논문 본문, 읽은 파일, 도구 출력 — 은
# 우리 계기에 잡히지 않는다. 오늘 연구 라운드에서 주민들이 논문 전문을
# 열어 읽었는데 우리 장부에는 호출당 1,600 토큰 정도만 남았다. 실제 소비는
# 그보다 훨씬 크다.
#
# 그래서: **웹·파일을 끌어오는 작업의 추정치는 하한으로만 읽는다.** 이
# 눈금으로 계산한 "1%당 토큰" 역시 그런 작업에서는 과대평가된다(적게 센
# 토큰으로 같은 %를 설명하게 되므로). 텍스트만 오가는 작업에서는 눈금이
# 비교적 맞는다.

# ── 계기의 한계 II: 공유 풀 + 이미지 토큰 (2026-08-15, 케어테이커 오류 정정) ─
# 처음에 codex 하루 하락(89→85%)이 우리 스팬(711k)보다 훨씬 크니 "len/4가
# 마을 작업을 100배 과소계상한다"고 적었다. **틀렸다** (설립자 정정): 그 하락의
# 대부분은 마을이 아니라 **설립자가 codex로 하는 다른 작업**이다. codex 풀은
# 공유이고, 마을 몫은 대체로 우리 스팬 추정에 가깝다. 하루-하락으로 마을
# 계수를 낼 수 없다 — 공유분을 뗄 수 없기 때문이다 (grok imagine·claude
# all-models와 같은 문제).
#
# 다만 **이미지 토큰은 len/4에 아예 안 잡힌다**는 것은 직접 확인됐다: codex가
# 이미지 한 장에 스스로 보고한 토큰 21,079 vs 같은 호출의 우리 추정 ~125.
# 이건 공유 풀과 무관한 호출 단위 사실이다 — 이미지 생성 비용은 텍스트 길이와
# 무관하므로 len/4가 0에 가깝게 센다. (텍스트-only codex 호출의 추론 토큰
# 과소분은 별개 질문이며 아직 깨끗이 재지 않았다 — 단정하지 않는다.)
#
# 그래서: codex quota는 (1) codex exec의 "tokens used N" 자가보고 — 호출 단위
# 정확 — 와 (2) 바닥으로 회계한다. 대시보드 %는 공유 풀이라 마을 몫 분리 불가.
