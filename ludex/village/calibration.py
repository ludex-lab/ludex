"""사용량 보정 — 우리의 추정 토큰을 구독의 실제 소진율에 붙인다.

재정 대장은 brain_call 스팬의 **추정 토큰**(어댑터의 길이 기반)으로 회계한다.
그것은 마을 안에서 배분을 보기에는 충분하지만, "이번 주 구독을 얼마나 썼는가"에는
답하지 못한다 — 공급자의 잔여율은 우리 계기가 볼 수 없는 값이기 때문이다.

설립자는 그 값을 볼 수 있다. 그래서 보정은 이렇게 한다 (JJ 2026-08-14):

  1. 무언가 돌리기 **직전** 창을 연다 — 그 시점의 부족별 누적 추정 토큰을 찍는다.
  2. 설립자가 그 시점의 **잔여 %** 를 함께 적는다.
  3. 일이 끝나면 창을 닫고, 끝난 시점의 잔여 %를 적는다.
  4. 두 값에서 **1%당 추정 토큰**이 나온다 = 우리 계기의 눈금을 구독의 눈금에
     붙이는 환산 계수.

계수는 브레인마다 다르고 크기마다 다르다 — opus 한 번이 haiku 한 번보다 훨씬
많은 구독을 먹는다. 그래서 창은 **공급자·모델 단위로 닫는다**. 계수 하나로
전부를 환산하지 않는다.

정직 규율: 이 계수는 **추정의 추정**이다. 잔여 %는 눈으로 읽은 값이고, 우리
토큰은 길이 기반 추정이며, 같은 창에 다른 일이 섞이면 오염된다. 그래서 각
창은 무엇이 그 안에서 돌았는지(label)를 함께 적고, 계수는 **n이 쌓이기 전에는
방향 참고로만** 쓴다. 단발 계수로 예산을 자르지 않는다.

    python -m ludex.village.calibration open  --label "dry-run" --provider claude_cli --pct 82
    python -m ludex.village.calibration close --label "dry-run" --pct 74
    python -m ludex.village.calibration table
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ludex.village.bus import REPO_ROOT, creature_dirs, _iter_spans

LEDGER = REPO_ROOT / "village" / "treasury" / "calibration.jsonl"

# 부족이 스스로 읽어 주는 잔여율 (2026-08-14 발견). 설립자가 눈으로 읽어
# 주는 값에 의존하지 않아도 되는 부족이 둘 있다 — 그쪽이 훨씬 정확하고
# 리셋 시각까지 함께 준다. codex는 자기 한도를 세션에서 볼 수 없다고
# 스스로 밝혔으므로(“Codex → Settings → Usage에서 보라”) 그 부족만
# 설립자의 눈에 의존한다. 없는 것을 있다고 적지 않는다.
SELF_READ = {
    "agy_cli": ["agy", "-p", "/usage", "--model", "gemini-3.7-flash",
                "--effort", "low"],
    "claude_cli": ["claude", "-p", "/usage", "--model", "claude-haiku-4-5"],
}


def read_usage(provider: str, model: str = "") -> dict:
    """Ask the tribe's own CLI what it has left. Returns raw text + parsed
    weekly-remaining percent when the shape is recognizable."""
    import re
    import shutil
    import subprocess
    from ludex.blocks.adapters._cli_env import cli_subprocess_env
    argv = SELF_READ.get(provider)
    if not argv:
        return {"supported": False,
                "note": "이 부족은 세션에서 자기 한도를 읽지 못한다 — 설립자의 눈에 의존"}
    binp = shutil.which(argv[0])
    if not binp:
        return {"supported": True, "error": f"{argv[0]} not on PATH"}
    try:
        r = subprocess.run([binp] + argv[1:], capture_output=True, text=True,
                           timeout=120, errors="replace",
                           env=cli_subprocess_env(provider, "subscription"),
                           stdin=subprocess.DEVNULL)
        text = (r.stdout or "").strip()
    except Exception as e:
        return {"supported": True, "error": f"{type(e).__name__}: {e}"[:200]}
    pct = None
    # 어느 버킷을 읽는지는 agy 분기에서도 반환에 실려야 하므로 먼저 정한다.
    bucket = "Fable" if "fable" in (model or "").lower() else "all models"
    # agy: "Gemini Models\tWeekly Limit Remaining\t100%\t<reset>"
    m = re.search(r"Weekly Limit Remaining\s+(\d+(?:\.\d+)?)%", text)
    if m:
        pct = float(m.group(1))
    else:
        # claude reports TWO buckets and they move independently:
        #   "Current week (all models): 63% used"
        #   "Current week (Fable): 100% used"
        # Reading only the first would credit Fable's burn to the shared pool
        # and hide a bucket that can exhaust on its own. Which bucket applies
        # depends on the model under measurement, so the caller says.
        m = re.search(rf"Current week \({re.escape(bucket)}\):\s*(\d+(?:\.\d+)?)%\s*used",
                      text)
        if m:
            pct = round(100.0 - float(m.group(1)), 2)
    return {"supported": True, "pct_remaining": pct, "raw": text[:800],
            "bucket": bucket if provider == "claude_cli" else ""}


def cumulative(provider: str = "", model: str = "") -> dict:
    """Current cumulative estimated tokens, optionally filtered."""
    out = {"calls": 0, "tok_in": 0, "tok_out": 0}
    for cdir in creature_dirs(REPO_ROOT / "creatures", ""):
        for span in _iter_spans(cdir):
            if span.get("kind") != "brain_call":
                continue
            a = span.get("attributes") or {}
            if provider and a.get("provider") != provider:
                continue
            if model and a.get("model") != model:
                continue
            out["calls"] += 1
            out["tok_in"] += int(a.get("tokens_in") or 0)
            out["tok_out"] += int(a.get("tokens_out") or 0)
    return out


def _rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]


def _append(row: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _resolve_pct(provider: str, given, model: str = ""):
    """Founder-read value wins when supplied; otherwise ask the tribe."""
    if given is not None:
        return given, "founder-read"
    u = read_usage(provider, model)
    if u.get("pct_remaining") is not None:
        return u["pct_remaining"], "self-read"
    raise SystemExit(f"잔여 %를 얻지 못했다 ({provider}): "
                     f"{u.get('note') or u.get('error') or 'unparsed'} — --pct로 직접 넣어라")


def cmd_open(a) -> int:
    a.pct, src = _resolve_pct(a.provider, a.pct, a.model)
    snap = cumulative(a.provider, a.model)
    _append({"ts": time.time(), "event": "open", "label": a.label,
             "provider": a.provider, "model": a.model,
             "pct_remaining": a.pct, "pct_source": src,
             "plan": getattr(a, "plan", ""), "cum": snap})
    print(f"창 열림 [{a.label}] {a.provider or 'all'}"
          f"{'/' + a.model if a.model else ''} — 잔여 {a.pct}% · "
          f"누적 {snap['calls']}건/{snap['tok_out']:,}tok(out)")
    return 0


def cmd_close(a) -> int:
    rows = _rows()
    pend = [r for r in rows if r.get("event") == "pending_close" and r["label"] == a.label]
    opens = [r for r in rows if r["event"] == "open" and r["label"] == a.label]
    if not opens:
        print(f"열린 창 없음: {a.label}")
        return 1
    o = opens[-1]
    # 정정은 지우지 않고 얹는다 — 판독기가 최신 정정을 따른다. (2026-08-14:
    # codex 잔여율을 설립자가 90%라 했는데 10%로 뒤집어 적은 일이 있었다.
    # 원장을 고쳐 쓰면 그런 실수가 흔적 없이 사라지고, 그러면 다음 실수를
    # 아무도 못 배운다.)
    for r in rows:
        if (r.get("event") == "correction" and r.get("corrects") == a.label
                and r.get("ts", 0) > o.get("ts", 0)):
            o = {**o, "pct_remaining": r["pct_remaining"],
                 "corrected_by": r.get("reason", "correction")}
    a.pct, src = _resolve_pct(o["provider"], a.pct, o.get("model", ""))
    # 설립자 읽기는 우리 창보다 드물게 온다. 같은 읽기(%) 위에서 여러 창이
    # 보류로 쌓였으면 그 사이 소비는 창 하나가 아니라 **합**이다 — 마지막
    # 창만으로 계수를 내면 분자를 깎아 계수를 과소평가한다. (2026-08-14:
    # 89% 위에 sol 라운드 셋이 쌓였다.)
    pooled = [s.strip() for s in (getattr(a, "pool", "") or "").split(",") if s.strip()]
    if pooled:
        d_out = d_in = d_calls = 0
        for lab in pooled:
            ps = [r for r in rows if r.get("event") == "pending_close" and r["label"] == lab]
            if not ps:
                print(f"  ! 보류 창 없음, 건너뜀: {lab}")
                continue
            d_out += ps[-1]["d_tok_out"]; d_in += ps[-1]["d_tok_in"]
            d_calls += ps[-1]["d_calls"]
    elif pend:
        p_ = pend[-1]
        d_out, d_in, d_calls = p_["d_tok_out"], p_["d_tok_in"], p_["d_calls"]
    else:
        snap = cumulative(o["provider"], o["model"])
        d_out = snap["tok_out"] - o["cum"]["tok_out"]
        d_in = snap["tok_in"] - o["cum"]["tok_in"]
        d_calls = snap["calls"] - o["cum"]["calls"]
    d_pct = o["pct_remaining"] - a.pct
    # 정수로 읽은 두 값의 차이는 점이 아니라 구간이다 (JJ 2026-08-14:
    # "1%라고 하기는 애매하지, 소숫점을 모르니까"). 표시 90은 실제
    # [89.5, 90.5), 표시 89는 [88.5, 89.5) — 그러므로 소진은 (0, 2)이고
    # 계수는 하한만 단단하다. 점 추정으로 적으면 거짓 정밀이다.
    tok = d_in + d_out
    lo_pct, hi_pct = max(d_pct - 1.0, 0.0), d_pct + 1.0   # 두 읽기의 반올림 오차
    per_pct = round(tok / d_pct) if d_pct > 0 else None
    per_floor = round(tok / hi_pct) if hi_pct > 0 else None   # 소진이 최대였다면
    per_ceiling = round(tok / lo_pct) if lo_pct > 0 else None # 최소였다면 (없으면 ∞)
    row = {"ts": time.time(), "event": "close", "label": a.label,
           "provider": o["provider"], "model": o["model"],
           "pct_remaining": a.pct, "pct_spent": round(d_pct, 2),
           "d_calls": d_calls, "d_tok_in": d_in, "d_tok_out": d_out,
           "est_tokens_per_pct": per_pct,
           "est_tokens_per_pct_floor": per_floor,
           "est_tokens_per_pct_ceiling": per_ceiling,
           "reading_resolution": "integer-percent",
           "pct_source": src, "plan": o.get("plan", ""), "note": a.note}
    _append(row)
    print(f"창 닫힘 [{a.label}] {o['provider'] or 'all'}"
          f"{'/' + o['model'] if o['model'] else ''}")
    print(f"  이 창에서: {d_calls}건 · 추정 in {d_in:,} · out {d_out:,}")
    print(f"  구독 소진: {d_pct:.2f}%p")
    if per_pct:
        ceil_s = f"{per_ceiling:,}" if per_ceiling else "∞ (소진이 반올림 아래일 수 있음)"
        print(f"  → 1%당 추정 토큰: **≥ {per_floor:,}**, 중간값 {per_pct:,}, 상한 {ceil_s}")
        print(f"     (정수 읽기의 반올림 때문에 소진은 {lo_pct:.1f}~{hi_pct:.1f}%p 구간이다 —"
              f" 단단한 것은 하한뿐이다)")
        print(f"     ※ 웹·파일을 끌어온 작업이면 우리 토큰 추정이 실제보다 적으므로,"
              f" 참 계수는 위 값보다 크다")
    else:
        print("  → 소진 0 또는 음수 — 계수 미산출 (창이 너무 짧거나 리셋이 끼었다)")
    return 0


def cmd_table() -> int:
    closes = [r for r in _rows() if r["event"] == "close" and r.get("est_tokens_per_pct")]
    if not closes:
        print("(보정 표본 없음)")
        return 0
    by: dict[str, list[int]] = {}
    for r in closes:
        key = (f"{r['provider'] or 'all'}{'/' + r['model'] if r.get('model') else ''}"
               f"{' [' + r['plan'] + ']' if r.get('plan') else ''}")
        by.setdefault(key, []).append(r["est_tokens_per_pct"])
    print(f"{'브레인':28} {'n':>3}  {'1%당 추정 토큰 (중앙값)':>22}")
    for key, vals in sorted(by.items()):
        vals.sort()
        med = vals[len(vals) // 2]
        flag = "  ← n=1, 방향 참고만" if len(vals) == 1 else ""
        print(f"{key:28} {len(vals):>3}  {med:>22,}{flag}")
    return 0


def cmd_correct(a) -> int:
    _append({"ts": time.time(), "event": "correction", "corrects": a.label,
             "pct_remaining": a.pct, "reason": a.reason})
    print(f"정정 등재 [{a.label}] → 잔여 {a.pct}% ({a.reason or '사유 미기재'})")
    return 0


def cmd_probe(a) -> int:
    """한 모델만 도는 창 — 모델별 무게를 재는 유일한 방법.

    부족 단위 창은 모델이 섞이면 계수를 모델에 붙이지 못한다 (sol/terra/luna,
    haiku/sonnet/opus/fable, flash/pro는 같은 풀을 서로 다른 속도로 먹는다).
    이 검사는 지정한 모델로만 같은 프롬프트를 n번 때리고 그 창의 소진율을
    읽어, **그 모델이 구독 1%를 먹는 데 드는 추정 토큰**을 낸다.

    무게(weight)는 그 역수로 읽으면 된다 — 1%당 토큰이 적을수록 비싼 모델이다.
    """
    import time as _t
    from ludex.blocks.provider import ADAPTER_REGISTRY
    import inspect
    label = a.label or f"probe-{a.provider}-{a.model}-{int(_t.time())}"
    pct0, src0 = _resolve_pct(a.provider, a.pct)
    cum0 = cumulative(a.provider, a.model)
    _append({"ts": _t.time(), "event": "open", "label": label,
             "provider": a.provider, "model": a.model, "pct_remaining": pct0,
             "pct_source": src0, "plan": a.plan, "cum": cum0,
             "kind": "single-model-probe"})
    print(f"창 열림 [{label}] {a.provider}/{a.model} — 잔여 {pct0}% ({src0})")

    cls = ADAPTER_REGISTRY[a.provider]
    adapter = cls(cwd="", auth="subscription", timeout_ms=300000)
    prompt = ("Write one paragraph (about 120 words) describing a small island "
              "village waking up in the morning. Plain prose, no lists.")
    # The probe calls the adapter directly, so nothing lands in a creature's
    # store and span-scanning sees zero. It must count its own tokens from the
    # responses — an instrument that cannot measure its own consumption reports
    # "0 calls" after burning eight (2026-08-14).
    ok, self_in, self_out = 0, 0, 0
    for i in range(a.n):
        kwargs = {"model": a.model, "prompt": prompt}
        if a.effort and "effort" in inspect.signature(cls.call).parameters:
            kwargs["effort"] = a.effort
        try:
            r = adapter.call(**kwargs)
            if (getattr(r, "content", "") or "").strip():
                ok += 1
            self_in += int(getattr(r, "tokens_in", 0) or 0)
            self_out += int(getattr(r, "tokens_out", 0) or 0)
        except Exception as e:
            print(f"  call {i+1}: {type(e).__name__}")
        print(f"  {i+1}/{a.n} done", flush=True)

    cum1 = cumulative(a.provider, a.model)
    # own accounting first; span-derived numbers only as a cross-check
    d_in = self_in or (cum1["tok_in"] - cum0["tok_in"])
    d_out = self_out or (cum1["tok_out"] - cum0["tok_out"])
    base = {"ts": _t.time(), "label": label, "provider": a.provider,
            "model": a.model, "plan": a.plan, "accounting": "probe-self",
            "d_calls": ok, "d_tok_in": d_in,
            "d_tok_out": d_out, "kind": "single-model-probe",
            "note": f"n={a.n}, ok={ok}"}

    # A tribe that cannot read its own quota must not lose the measurement.
    # The spend already happened; parking the window keeps it usable the
    # moment the founder supplies the number. (Earlier this crashed at close
    # and threw away eight real calls — 2026-08-14.)
    u = read_usage(a.provider)
    if u.get("pct_remaining") is None:
        _append({**base, "event": "pending_close", "pct_remaining": None,
                 "pct_source": "awaiting-founder"})
        print(f"창 보류 [{label}] — 호출 {ok}/{a.n} · 추정 in {d_in:,} out {d_out:,}")
        print(f"  이 부족은 자기 한도를 못 읽는다. 설립자가 잔여 %를 알려주면:")
        print(f"  python -m ludex.village.calibration close --label {label} --pct <값>")
        return 0

    pct1, src1 = u["pct_remaining"], "self-read"
    d_pct = pct0 - pct1
    per_pct = round((d_in + d_out) / d_pct) if d_pct > 0 else None
    _append({**base, "event": "close", "pct_remaining": pct1,
             "pct_source": src1, "pct_spent": round(d_pct, 3),
             "est_tokens_per_pct": per_pct})
    print(f"창 닫힘 — 호출 {ok}/{a.n} · 추정 in {d_in:,} out {d_out:,} · 소진 {d_pct:.3f}%p")
    if per_pct:
        print(f"  → {a.model}: **1%당 추정 {per_pct:,} 토큰**")
    else:
        # A null result is not "unlimited" — it is a LOWER BOUND. If n calls did
        # not move an integer-percent needle, then 1% costs more than n calls,
        # so the weekly pool holds at least 100n. Saying "below resolution" and
        # stopping there invites the reading that budget is infinite; the bound
        # is the honest form of the same fact (JJ, 2026-08-14).
        lb_calls = ok * 100
        lb_tok = (d_in + d_out) * 100
        print(f"  → 1%를 못 움직였다 = **하한**: 주간 풀 ≥ {lb_calls:,}회 "
              f"(≥ 추정 {lb_tok:,} 토큰) 상당. 무한이 아니라 '적어도 이만큼'이다.")
        _append({"ts": _t.time(), "event": "bound", "label": label,
                 "provider": a.provider, "model": a.model, "plan": a.plan,
                 "n_calls": ok, "d_tok_in": d_in, "d_tok_out": d_out,
                 "weekly_lower_bound_calls": lb_calls,
                 "weekly_lower_bound_tokens": lb_tok,
                 "note": "null at 1% resolution → lower bound"})
    return 0


def cmd_around(a) -> int:
    """일을 창으로 감싼다 — 여는 것을 잊을 수 없게.

    오늘 두 번, 케어테이커가 창을 열기 전에 작업을 띄웠다. 순서를 기억에
    맡기면 또 틀린다. 이 명령은 열고·돌리고·닫는 것을 한 번에 한다.
    합성 프로브 대신 **진짜 마을 일**을 감싸는 것이 옳다 — 계기가 재려는
    것보다 무거우면 안 되고, 어차피 마을은 그 일을 해야 하기 때문이다.
    """
    import subprocess as _sp
    import time as _t
    provs = [p.strip() for p in a.providers.split(",") if p.strip()]
    # Founder-supplied starts for tribes that cannot read their own quota,
    # as "prov=value" pairs. Resolve EVERY provider before opening ANY window —
    # a half-open state (one window open, the run never started) is worse than
    # a clean refusal, and that is exactly what happened on the first try.
    given = {}
    for item in (a.pct0 or "").split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            given[k.strip()] = float(v)
    starts = {}
    for prov in provs:
        val = given.get(prov)
        if val is None:
            u = read_usage(prov)
            val = u.get("pct_remaining")
            src = "self-read"
            if val is None:
                raise SystemExit(
                    f"창을 하나도 열지 않았다 — {prov}의 잔여 %를 얻을 수 없다 "
                    f"({u.get('note') or u.get('error') or 'unparsed'}). "
                    f"--pct0 {prov}=<값> 으로 넣어라.")
        else:
            src = "founder-read"
        starts[prov] = (val, src)
    opened = []
    for prov in provs:
        pct, src = starts[prov]
        cum = cumulative(prov, a.model)
        label = f"{a.label}-{prov}"
        _append({"ts": _t.time(), "event": "open", "label": label,
                 "provider": prov, "model": a.model, "pct_remaining": pct,
                 "pct_source": src, "plan": a.plan, "cum": cum,
                 "kind": "work-window", "work": a.run[:200]})
        opened.append((label, prov, pct, src))
        print(f"창 열림 [{label}] 잔여 {pct}% ({src})")
    print(f"--- 작업 시작: {a.run[:90]} ---", flush=True)
    rc = _sp.run(a.run, shell=True, cwd=str(REPO_ROOT)).returncode
    print(f"--- 작업 끝 (rc={rc}) ---")
    for label, prov, pct0, _ in opened:
        u = read_usage(prov)
        cum1 = cumulative(prov, a.model)
        opens = [r for r in _rows() if r.get("label") == label and r["event"] == "open"]
        c0 = opens[-1]["cum"]
        d_in = cum1["tok_in"] - c0["tok_in"]
        d_out = cum1["tok_out"] - c0["tok_out"]
        d_calls = cum1["calls"] - c0["calls"]
        if u.get("pct_remaining") is None:
            _append({"ts": _t.time(), "event": "pending_close", "label": label,
                     "provider": prov, "d_calls": d_calls, "d_tok_in": d_in,
                     "d_tok_out": d_out, "kind": "work-window"})
            print(f"[{label}] 보류 — {d_calls}건 · in {d_in:,} out {d_out:,} "
                  f"· 설립자 잔여 % 필요")
            continue
        d_pct = pct0 - u["pct_remaining"]
        per = round((d_in + d_out) / d_pct) if d_pct > 0 else None
        _append({"ts": _t.time(), "event": "close" if per else "bound",
                 "label": label, "provider": prov, "plan": a.plan,
                 "pct_remaining": u["pct_remaining"], "pct_source": "self-read",
                 "pct_spent": round(d_pct, 3), "d_calls": d_calls,
                 "d_tok_in": d_in, "d_tok_out": d_out,
                 "est_tokens_per_pct": per,
                 "weekly_lower_bound_tokens": None if per else (d_in + d_out) * 100,
                 "kind": "work-window"})
        if per:
            print(f"[{label}] {d_calls}건 · 소진 {d_pct:.2f}%p → **1%당 추정 {per:,} 토큰**")
        else:
            print(f"[{label}] {d_calls}건 · in {d_in:,} out {d_out:,} · 소진 0 "
                  f"→ 하한: 주간 ≥ 추정 {(d_in+d_out)*100:,} 토큰")
    return rc


def cmd_usage(a) -> int:
    provs = [a.provider] if a.provider else ["agy_cli", "claude_cli", "codex_cli"]
    for p in provs:
        u = read_usage(p)
        if not u.get("supported"):
            print(f"{p:11} — 자기 열람 불가 ({u['note']})")
        elif u.get("error"):
            print(f"{p:11} — 오류: {u['error']}")
        else:
            print(f"{p:11} 잔여 {u['pct_remaining']}%")
            for line in (u["raw"] or "").splitlines()[:4]:
                if line.strip():
                    print(f"            | {line.strip()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("open")
    o.add_argument("--label", required=True)
    o.add_argument("--plan", default="", help="subscription tier this window is measured on")
    o.add_argument("--provider", default="")
    o.add_argument("--model", default="")
    o.add_argument("--pct", type=float, default=None, help="remaining quota percent; omit to let the tribe read its own")
    c = sub.add_parser("close")
    c.add_argument("--label", required=True)
    c.add_argument("--pct", type=float, default=None)
    c.add_argument("--note", default="")
    c.add_argument("--pool", default="",
                   help="comma-separated pending labels to sum into this close "
                        "(use when several windows piled up under one reading)")
    cor = sub.add_parser("correct")
    cor.add_argument("--label", required=True)
    cor.add_argument("--pct", type=float, required=True)
    cor.add_argument("--reason", default="")
    sub.add_parser("table")
    u = sub.add_parser("usage")
    u.add_argument("--provider", default="")
    pr = sub.add_parser("probe")
    pr.add_argument("--provider", required=True)
    pr.add_argument("--model", required=True)
    pr.add_argument("--n", type=int, default=6)
    pr.add_argument("--effort", default="")
    pr.add_argument("--plan", default="")
    pr.add_argument("--label", default="")
    pr.add_argument("--pct", type=float, default=None)
    ar = sub.add_parser("around")
    ar.add_argument("--label", required=True)
    ar.add_argument("--providers", required=True, help="comma-separated")
    ar.add_argument("--plan", default="")
    ar.add_argument("--run", required=True, help="shell command to wrap in the window")
    ar.add_argument("--pct0", default="", help="starts for tribes that cannot self-read: prov=value,prov=value")
    ar.add_argument("--model", default="", help="restrict the token side to one model (per-model coefficients)")
    a = ap.parse_args()
    return {"open": cmd_open, "close": cmd_close,
            "table": lambda _a: cmd_table(),
            "usage": cmd_usage, "probe": cmd_probe,
            "correct": cmd_correct, "around": cmd_around}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
