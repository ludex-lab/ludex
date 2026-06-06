"""Cost & usage rollup for the Metabolic Gauge (D-081).

Reads `brain_call` spans from every creature's `store/spans.jsonl`,
joins them against `config/model_pricing.yaml`, and rolls the result
up by creature, by provider, by call-type (maintenance vs experiment),
and by day.

This is the read side of the Metabolic Gauge — see
`docs/metabolic-gauge-design.md`. Stage 1's reliable output is
token/call VOLUME; the cost column is only as trustworthy as the
pricing table.

Call-type derivation (Stage 1): a brain_call span with an empty
`field_name` is counted as `maintenance` (heartbeat / reflect);
a populated `field_name` is counted as `experiment`. Finer
correlation against neighbouring spans is a later refinement.

Stage 2.0 (this file): alert evaluation against design-§10
thresholds, with exit codes for scheduled/cron callers.
- Exit 0: clean (no alerts).
- Exit 1: one or more WARNING-level alerts fired.
- Exit 2: one or more CRITICAL alerts fired (regardless of warnings).
Use `--alerts-only` to suppress tables and emit just the alert
section (e.g. for caretaker summaries). Trend-based warnings
(cost upward, latency spike, token drift) require prior-window
baselines and land in Stage 2.1.

Usage:
    PYTHONPATH=. .venv/bin/python tools/cost_report.py [--creature NAME] [--alerts-only]
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRICING_PATH = REPO_ROOT / "config" / "model_pricing.yaml"
SPANS_GLOB = str(REPO_ROOT / "creatures" / "*" / "store" / "spans.jsonl")

# Stage 2.0 alert thresholds — derived from design-§10.5 baseline
# (2026-05-26 cohort, verified pricing). Re-tune at the 30-day mark.
DAILY_CRITICAL_CAP_USD = 0.50          # any single day > cap → CRITICAL
ERROR_RATE_CRITICAL = 0.50             # non-dorm err / non-dorm calls > 50% → CRITICAL
PRICING_STALE_DAYS = 90                # model_pricing.yaml older than N days → CRITICAL
MODEL_ERROR_RECURRING_THRESHOLD = 3    # same (creature, model) model_error events ≥ N → WARNING


def load_pricing() -> tuple[dict, bool, str]:
    """Returns (models_dict, prices_verified_bool, updated_iso_str).
    updated_iso_str is "" if absent — caller skips staleness check."""
    import yaml
    data = yaml.safe_load(PRICING_PATH.read_text(encoding="utf-8")) or {}
    return (data.get("models", {}) or {},
            bool(data.get("prices_verified", False)),
            str(data.get("updated", "")))


def call_cost(model: str, tin: int, tout: int, pricing: dict) -> float | None:
    """USD cost for one call, or None if the model is unpriced."""
    p = pricing.get(model)
    if p is None:
        return None
    return (tin / 1_000_000) * p.get("in", 0.0) + (tout / 1_000_000) * p.get("out", 0.0)


def iter_brain_calls(creature_filter: str = ""):
    for path in sorted(glob.glob(SPANS_GLOB)):
        creature = Path(path).parent.parent.name
        if creature_filter and creature != creature_filter:
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                if '"brain_call"' not in line:
                    continue
                try:
                    span = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if span.get("kind") != "brain_call":
                    continue
                yield creature, span


def _row() -> dict:
    return {"calls": 0, "ok": 0, "err": 0, "dorm": 0, "tin": 0, "tout": 0,
            "cost": 0.0, "unpriced": 0}


def _is_dormant(span: dict) -> bool:
    """Intentional dormancy heuristic — separates expected silent state
    from real failure. Currently: ollama provider + connection-class
    error_type. See memory `moss-intentional-dormancy`: Moss/ollama on
    Mac is intentionally off by default to spare host RAM, so its
    `connection` errors are operational state, not malfunction.
    Extend this rule for other provider+error combinations if/when
    new intentional-dormancy patterns surface."""
    a = span.get("attributes", {})
    return (a.get("provider") == "ollama"
            and a.get("error_type") == "connection")


def _add(row: dict, span: dict, cost: float | None) -> None:
    a = span.get("attributes", {})
    row["calls"] += 1
    if a.get("outcome") == "ok":
        row["ok"] += 1
    elif _is_dormant(span):
        row["dorm"] += 1
    else:
        row["err"] += 1
    row["tin"] += int(a.get("tokens_in", 0) or 0)
    row["tout"] += int(a.get("tokens_out", 0) or 0)
    if cost is None:
        row["unpriced"] += 1
    else:
        row["cost"] += cost


def _print_table(title: str, rows: dict, *, span_days: int = 0) -> None:
    """span_days=0 suppresses 30d projection column; pass days observed
    to enable per-row monthly projection at current cadence."""
    print(f"\n-- {title} --")
    proj_hdr = f"{'30d_proj':>11}" if span_days else ""
    print(f"{'key':<22}{'calls':>7}{'ok':>5}{'err':>5}{'dorm':>5}"
          f"{'tok_in':>12}{'tok_out':>12}{'est_cost':>11}{proj_hdr}")
    for key in sorted(rows):
        r = rows[key]
        flag = " *" if r["unpriced"] else ""
        proj_col = ""
        if span_days:
            proj = r["cost"] * 30 / span_days
            proj_col = f"{'$' + format(proj, '.2f'):>11}"
        print(f"{key:<22}{r['calls']:>7}{r['ok']:>5}{r['err']:>5}{r['dorm']:>5}"
              f"{r['tin']:>12,}{r['tout']:>12,}"
              f"{'$' + format(r['cost'], '.4f'):>11}{proj_col}{flag}")


def _evaluate_alerts(by_creature: dict, by_day: dict, total: dict,
                     model_errors: dict, pricing_updated: str) -> tuple[list, int]:
    """Evaluate Stage 2.0 design-§10 thresholds against the rolled-up
    aggregates. Returns (alerts, max_severity) where severity is
    0 (clean), 1 (warning), 2 (critical).

    Implemented (Stage 2.0):
    - CRITICAL daily cost > DAILY_CRITICAL_CAP_USD on any in-window day.
    - CRITICAL per-creature non-dorm err rate > ERROR_RATE_CRITICAL.
    - CRITICAL any unpriced model encountered (cost blind spot).
    - CRITICAL pricing config older than PRICING_STALE_DAYS.
    - WARNING recurring model_error pattern (same creature × model
      ≥ MODEL_ERROR_RECURRING_THRESHOLD events in window).

    Deferred to Stage 2.1+ (need prior-window baseline persistence):
    - WARNING 30-day cost projection +50% week-over-week.
    - WARNING provider latency p90 spike ≥2x baseline.
    - WARNING per-creature tokens-per-call drift ≥2x rolling baseline.
    - Newborn grace skip — currently no trend warnings to skip from.
    """
    alerts: list[tuple[int, str, str]] = []  # (severity, code, message)

    for day in sorted(by_day):
        cost = by_day[day]["cost"]
        if cost > DAILY_CRITICAL_CAP_USD:
            alerts.append((2, "DAILY_COST_OVER_CAP",
                f"{day} cost ${cost:.4f} > ${DAILY_CRITICAL_CAP_USD:.2f} cap"))

    for creature in sorted(by_creature):
        r = by_creature[creature]
        non_dorm = r["ok"] + r["err"]
        if non_dorm == 0:
            continue
        rate = r["err"] / non_dorm
        if rate > ERROR_RATE_CRITICAL:
            alerts.append((2, "CREATURE_ERROR_RATE",
                f"{creature} non-dorm err rate {rate*100:.0f}% "
                f"({r['err']}/{non_dorm}) > {int(ERROR_RATE_CRITICAL*100)}% cap"))

    if total["unpriced"] > 0:
        alerts.append((2, "UNPRICED_MODEL",
            f"{total['unpriced']} call(s) on model(s) absent from "
            f"model_pricing.yaml — cost blind spot, add entries"))

    if pricing_updated:
        try:
            ud = date.fromisoformat(pricing_updated)
            age = (date.today() - ud).days
            if age > PRICING_STALE_DAYS:
                alerts.append((2, "PRICING_STALE",
                    f"model_pricing.yaml updated {age}d ago "
                    f"(>{PRICING_STALE_DAYS}d cap) — re-verify"))
        except ValueError:
            pass

    for (creature, model), count in sorted(model_errors.items()):
        if count >= MODEL_ERROR_RECURRING_THRESHOLD:
            alerts.append((1, "MODEL_ERROR_RECURRING",
                f"{creature} × {model} {count} model_error events in window"))

    alerts.sort(key=lambda a: (-a[0], a[1]))
    max_sev = max((a[0] for a in alerts), default=0)
    return alerts, max_sev


def _print_alerts(alerts: list) -> None:
    print("\n== alerts ==")
    if not alerts:
        print("  clean (no thresholds tripped)")
        return
    print(f"  {len(alerts)} fired:")
    for sev, code, msg in alerts:
        label = "CRITICAL" if sev == 2 else "WARNING" if sev == 1 else "INFO"
        print(f"  {label:<9} [{code:<25}] {msg}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Metabolic Gauge cost/usage rollup")
    ap.add_argument("--creature", default="", help="filter to one creature")
    ap.add_argument("--alerts-only", action="store_true",
                    help="suppress tables; emit only the alert section "
                         "(for caretaker / cron use). Exit code semantics "
                         "unchanged.")
    args = ap.parse_args()

    pricing, verified, pricing_updated = load_pricing()

    by_creature: dict = defaultdict(_row)
    by_provider: dict = defaultdict(_row)
    by_calltype: dict = defaultdict(_row)
    by_day: dict = defaultdict(_row)
    by_source: dict = defaultdict(int)
    model_errors: dict = defaultdict(int)  # (creature, model) -> model_error count
    total = _row()
    n = 0
    days: list[str] = []

    for creature, span in iter_brain_calls(args.creature):
        a = span.get("attributes", {})
        model = a.get("model", "")
        provider = a.get("provider", "") or "(unknown)"
        cost = call_cost(model, int(a.get("tokens_in", 0) or 0),
                         int(a.get("tokens_out", 0) or 0), pricing)
        calltype = "maintenance" if not a.get("field_name") else "experiment"
        ts = span.get("timestamp", 0)
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "unknown"
        days.append(day)
        by_source[a.get("token_source", "estimated")] += 1
        if a.get("error_type") == "model_error":
            model_errors[(creature, model)] += 1

        _add(by_creature[creature], span, cost)
        _add(by_provider[provider], span, cost)
        _add(by_calltype[calltype], span, cost)
        _add(by_day[day], span, cost)
        _add(total, span, cost)
        n += 1

    if not args.alerts_only:
        print("=== Metabolic Gauge — cost & usage report ===")
        print(f"spans: creatures/*/store/spans.jsonl  |  pricing: {PRICING_PATH.name}")
    if n == 0:
        if not args.alerts_only:
            print("\nNo brain_call spans found. "
                  "(The instrument emits from the first brain call after deployment.)")
        return 0
    if not verified and not args.alerts_only:
        print("WARNING prices_verified=false — est_cost uses PLACEHOLDER rates; "
              "treat as order-of-magnitude only. Token/call volume is reliable.")
    distinct_days = len(set(days))
    if not args.alerts_only:
        print(f"\nbrain_call spans: {n}   "
              f"window: {min(days)} -> {max(days)} ({distinct_days} day"
              f"{'s' if distinct_days != 1 else ''})")
        src = ", ".join(f"{k}={v}" for k, v in sorted(by_source.items()))
        print(f"token_source: {src}")
        print("dorm = intentional dormancy (currently: ollama + connection-error). "
              "Counted separately from err so threshold alerts don't false-fire.")
        if distinct_days >= 3:
            print(f"30d_proj = cost * 30 / {distinct_days} — assumes current cadence "
                  "holds. Field-session activity not in window inflates real cost.")
        else:
            print(f"30d_proj suppressed (window {distinct_days}d, "
                  "need >=3 days for stable projection).")

        proj_days = distinct_days if distinct_days >= 3 else 0
        _print_table("by provider", by_provider, span_days=proj_days)
        _print_table("by creature", by_creature, span_days=proj_days)
        _print_table("by call-type", by_calltype, span_days=proj_days)
        _print_table("by day", by_day)  # daily rows — no projection
        _print_table("total", {"ALL": total}, span_days=proj_days)
        if total["unpriced"]:
            print(f"\n* {total['unpriced']} call(s) on a model absent from "
                  f"model_pricing.yaml — counted in volume, $0 in cost.")

    alerts, max_sev = _evaluate_alerts(by_creature, by_day, total,
                                       model_errors, pricing_updated)
    _print_alerts(alerts)
    return max_sev  # 0 clean / 1 warning / 2 critical


if __name__ == "__main__":
    raise SystemExit(main())
