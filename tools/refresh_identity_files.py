"""신원 파일 재생성 — 기판·역할이 바뀌면 공개 의무 문서가 따라와야 한다.

이웃 랩(LxM)이 2026-08-20에 세운 트리거의 우리 집 적용: 미고정 구간은 시간이
아니라 **사건**에서 생기므로 정기 점검으로는 못 잡는다. 그래서 트리거는
"역할·권한·기판이 바뀐 순간 → 그 주체의 공개 의무 문서를 재독"이다.

우리 집에서 이 병은 두 번 나왔다. Aria의 CLAUDE.md는 07-27 재브레인 뒤 3주간
낡은 브레인 이름을 달고 있었고(08-19 그의 청구로 수정), Spark·Flare·Saga는
08-19 시술 뒤 하루 만에 같은 상태가 됐다. 두 번 다 손으로 고쳤고 기계는
없었다 — 그래서 세 번째가 왔다.

    python tools/refresh_identity_files.py --check      # 낡은 것만 보고
    python tools/refresh_identity_files.py <이름> ...    # 재생성
    python tools/refresh_identity_files.py --all        # 낡은 것 전부
"""
from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def _declared(path: str) -> tuple[str, str] | None:
    """CLAUDE.md가 선언하는 (model, provider)."""
    try:
        text = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return None
    m = re.search(r"\*\*Brain:\*\*\s*([^\s(]+)\s*\(via\s+([^)]+)\)", text)
    if not m:
        return None
    # provider 뒤의 부연("agy_cli / Antigravity")은 표기 차이지 상태 차이가
    # 아니다 — Ray 마을 Wick이 그렇게 쓴다. 남의 집 표기를 고치는 대신 우리
    # 자를 좁힌다(HABITATS.md: Ray-habitat은 이 머신에서 읽기 전용).
    provider = m.group(2).split("/")[0].strip()
    return (m.group(1), provider)


def survey() -> list[dict]:
    from ludex.core.organism_config import OrganismConfig
    rows = []
    for name in sorted(os.listdir(os.path.join(REPO, "creatures"))):
        d = os.path.join(REPO, "creatures", name)
        if not os.path.isfile(os.path.join(d, "ludex.yaml")):
            continue
        cfg = OrganismConfig.load(d)
        actual = (cfg.brain.get("model", ""), cfg.brain.get("provider", ""))
        declared = _declared(os.path.join(d, "CLAUDE.md"))
        if declared is None:
            continue                      # 신원 파일 없는 크리처는 대상 아님
        rows.append({"creature": name, "actual": actual, "declared": declared,
                     "stale": declared != actual})
    return rows


def refresh(name: str) -> bool:
    from ludex.core.organism_config import OrganismConfig
    d = os.path.join(REPO, "creatures", name)
    cfg = OrganismConfig.load(d)
    return bool(cfg.habitat.write_identity_files(
        creature_name=name,
        brain_model=cfg.brain.get("model", ""),
        brain_provider=cfg.brain.get("provider", ""),
        organs=cfg.get_enabled_organs(),
        custom_instructions=(cfg.organs.get("engine", {}) or {}).get("system_prompt", ""),
    ))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rows = survey()
    stale = [r for r in rows if r["stale"]]
    if "--check" in sys.argv or (not args and "--all" not in sys.argv):
        print(f"=== 신원 파일 점검 — {len(rows)}명 중 낡은 것 {len(stale)}명")
        for r in stale:
            print(f"  ● {r['creature']:10} 선언 {r['declared'][0]}/{r['declared'][1]}"
                  f"  ≠ 실제 {r['actual'][0]}/{r['actual'][1]}")
        if not stale:
            print("  ○ 전원 일치")
        return 0
    targets = args or [r["creature"] for r in stale]
    for name in targets:
        ok = refresh(name)
        print(f"  {'✓' if ok else '✗'} {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
