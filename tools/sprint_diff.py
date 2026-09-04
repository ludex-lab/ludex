#!/usr/bin/env python3
"""잘린 문장 — 초안 대 최종. 초안의 문장 중 최종에 (거의) 그대로 남지 않은 것을 절별로 나열한다.

    python tools/sprint_diff.py <draft.md> <final.md> [--threshold 0.6]

'남았다'의 기준은 문장 단위 유사도(difflib ratio ≥ threshold, 또는 핵심 어절 4개 이상 공유).
숫자가 든 문장은 따로 표시한다 — 숫자가 잘렸으면 근거가 잘린 것이다.
"""
import difflib
import re
import sys


def sentences(md: str) -> list[tuple[str, str]]:
    """Paragraph-aware: hard-wrapped lines are joined before sentence splitting
    (the caretaker's drafts wrap at ~80 columns; a wrapped line is not a sentence)."""
    out, sec, para = [], "(머리)", []

    def flush():
        text = re.sub(r"[*`_]", "", " ".join(para)).strip()
        for s in re.split(r"(?<=[.다!?])\s+", text):
            s = s.strip()
            if len(s) >= 12:
                out.append((sec, s))
        para.clear()

    for line in md.splitlines():
        if line.startswith("#"):
            flush(); sec = line.strip("# ").strip(); continue
        if not line.strip() or line.startswith("|"):
            flush(); continue
        if line.strip().startswith("*") and line.strip().endswith("*") and len(line) < 200:
            flush(); continue                      # italic meta lines
        para.append(line.strip("- ").strip())
    flush()
    return out


def kept(s: str, final_sents: list[str], thr: float) -> bool:
    words = set(w for w in re.findall(r"[가-힣A-Za-z0-9]+", s) if len(w) > 1)
    for f in final_sents:
        if difflib.SequenceMatcher(None, s, f).ratio() >= thr:
            return True
        fw = set(w for w in re.findall(r"[가-힣A-Za-z0-9]+", f) if len(w) > 1)
        if len(words & fw) >= max(4, int(0.6 * len(words))):
            return True
    return False


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__); return 2
    thr = float(sys.argv[sys.argv.index("--threshold") + 1]) if "--threshold" in sys.argv else 0.6
    draft = open(sys.argv[1], encoding="utf-8").read()
    final = open(sys.argv[2], encoding="utf-8").read()
    fs = [s for _, s in sentences(final)]
    ds = sentences(draft)
    cut = [(sec, s) for sec, s in ds if not kept(s, fs, thr)]
    print(f"# 잘린 문장 — 초안 {len(ds)}문장 중 {len(cut)}문장이 최종에 없다 (유지 {len(ds) - len(cut)})\n")
    cur = None
    for sec, s in cut:
        if sec != cur:
            print(f"\n## {sec}"); cur = sec
        flag = " ⟨숫자⟩" if re.search(r"\d", s) else ""
        print(f"- {s}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
