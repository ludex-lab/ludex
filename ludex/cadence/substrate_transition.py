"""
Substrate-transition record — write a substrate_transition span when a
creature's substrate changes on any axis (M model / A access / P provider
auth tier; see D-082 substrate change policy + D-086 harness-axis design).

No automation: per substrate_change_policy, a substrate change is a
deliberate caretaker act (preserve / transplant / death). This module only
*records* one after the caretaker performs it, so the transition is a
first-class event in the creature's store and the creature can perceive
"my substrate changed" on its next session.

D-086 specced the span for A-axis transitions
({from_provider, to_provider, model, a_magnitude, op, ...}); this is the
generalized form: an explicit `axis` plus from/to descriptors, so the same
span kind covers M-axis upgrades and P-axis auth swaps (first use:
2026-06 gemini consumer→paid-key migration).

Usage:
    python -m ludex.cadence.substrate_transition \
        --creature Nimbus --axis P \
        --from consumer_subscription --to paid_api_key \
        --magnitude tiny --op preserve \
        --note "gemini consumer auth retirement 2026-06-18"
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ludex.cadence.proposer import CREATURES_ROOT
from ludex.core.store import LudexStore, Span

logger = logging.getLogger(__name__)

AXES = ("M", "A", "P")
MAGNITUDES = ("tiny", "medium", "large")
OPS = ("preserve", "fork", "transplant")


def record_transition_span(
    store: LudexStore,
    name: str,
    *,
    axis: str,
    from_desc: str,
    to_desc: str,
    provider: str | None = None,
    model: str | None = None,
    magnitude: str = "tiny",
    op: str = "preserve",
    pre_snapshot: str | None = None,
    post_snapshot: str | None = None,
    note: str | None = None,
) -> bool:
    """Write a substrate_transition span. Returns True when written.

    Idempotent on the standing fact: if the latest substrate_transition
    span already records the same (axis, from, to, model, op), nothing is
    written — re-running the command after a partially-failed swap does
    not duplicate the event. A changed `op` (e.g. a later preserve→transplant
    RECLASSIFICATION) is a new recordable event, not a duplicate.
    """
    if axis not in AXES:
        raise ValueError(f"axis must be one of {AXES}, got {axis!r}")
    if magnitude not in MAGNITUDES:
        raise ValueError(f"magnitude must be one of {MAGNITUDES}, got {magnitude!r}")
    if op not in OPS:
        raise ValueError(f"op must be one of {OPS}, got {op!r}")

    prior = store.spans(kind="substrate_transition", limit=1)
    if prior:
        a = prior[-1].get("attributes", {})
        if (
            a.get("axis") == axis
            and a.get("from") == from_desc
            and a.get("to") == to_desc
            and a.get("model") == model
            and a.get("op") == op
        ):
            return False

    store.append(Span(
        kind="substrate_transition",
        creature=name,
        attributes={
            "axis": axis,
            "from": from_desc,
            "to": to_desc,
            "provider": provider,
            "model": model,
            "magnitude": magnitude,
            "op": op,
            "pre_snapshot": pre_snapshot,
            "post_snapshot": post_snapshot,
            "note": note,
        },
    ))
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="record a substrate_transition span in a creature's store",
    )
    ap.add_argument("--creature", required=True, help="creature name")
    ap.add_argument("--root", default=CREATURES_ROOT, help="creatures root dir")
    ap.add_argument("--axis", required=True, choices=AXES,
                    help="which substrate axis changed (M model / A access / P auth)")
    ap.add_argument("--from", dest="from_desc", required=True,
                    help="substrate descriptor before the change")
    ap.add_argument("--to", dest="to_desc", required=True,
                    help="substrate descriptor after the change")
    ap.add_argument("--provider", default=None, help="adapter provider (unchanged unless A-axis)")
    ap.add_argument("--model", default=None, help="model id (unchanged unless M-axis)")
    ap.add_argument("--magnitude", default="tiny", choices=MAGNITUDES)
    ap.add_argument("--op", default="preserve", choices=OPS)
    ap.add_argument("--pre-snapshot", default=None, help="path of the pre-transition snapshot")
    ap.add_argument("--post-snapshot", default=None, help="path of the post-transition snapshot")
    ap.add_argument("--note", default=None, help="free-form caretaker note")
    args = ap.parse_args(argv)

    habitat = Path(args.root) / args.creature
    if not habitat.is_dir():
        logger.error(f"no creature habitat at {habitat}")
        return 1
    store = LudexStore.for_creature(str(habitat))

    written = record_transition_span(
        store, args.creature,
        axis=args.axis, from_desc=args.from_desc, to_desc=args.to_desc,
        provider=args.provider, model=args.model,
        magnitude=args.magnitude, op=args.op,
        pre_snapshot=args.pre_snapshot, post_snapshot=args.post_snapshot,
        note=args.note,
    )
    if written:
        print(f"substrate_transition span written: {args.creature} "
              f"[{args.axis}] {args.from_desc} → {args.to_desc} ({args.op})")
    else:
        print(f"already recorded for {args.creature} — nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
