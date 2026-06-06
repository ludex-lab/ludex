"""Stacker engine — PlanBench-style block-stacking task.

Single-agent fully-observable. Implements the contract declared in
`fields/stacker/world_schema.json`:

State:
- `on_table`: set of block ids on the table (each block is also
  the bottom of its stack, possibly with blocks on top)
- `stacks`: dict block → block (key = lower block, value = block
  immediately above). So a stack of A/B/C (C on top) is stored as
  `{"A": "B", "B": "C"}` plus `"A" in on_table`.
- `in_hand`: block id currently held, or empty string if none
- `goal_stacks` + `goal_on_table`: target configuration

Actions: pickup / putdown / stack / unstack — see schema for
constraints.

Reward:
- Dense intermediate: goal_distance(prev) - goal_distance(current)
  (positive when state moves toward goal)
- Terminal: +1.0 if goal_satisfied within step_cap; 0.0 otherwise
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StackerInstance:
    """A single Stacker problem.

    `optimal_steps` may be None when the optimum isn't known
    (custom instances, exploratory). Used for `optimal_ratio`
    in trace last_line_meta.
    """
    instance_id: str
    blocks: list[str]
    initial_stacks: dict[str, str]
    initial_on_table: list[str]
    goal_stacks: dict[str, str]
    goal_on_table: list[str]
    optimal_steps: Optional[int] = None
    difficulty: str = ""
    description: str = ""


@dataclass
class StackerState:
    """Mutable Stacker state during a run."""
    blocks: list[str]
    on_table: set[str]
    stacks: dict[str, str]
    in_hand: str = ""
    goal_stacks: dict[str, str] = field(default_factory=dict)
    goal_on_table: set[str] = field(default_factory=set)
    step: int = 0
    step_cap: int = 20

    @property
    def hand_empty(self) -> bool:
        return not self.in_hand

    @property
    def goal_satisfied(self) -> bool:
        if self.in_hand:
            # Goal config has all blocks placed; can't be satisfied while holding.
            return False
        if self.on_table != self.goal_on_table:
            return False
        if self.stacks != self.goal_stacks:
            return False
        return True

    def is_clear(self, block: str) -> bool:
        """No block is on top of `block`."""
        return block not in self.stacks

    def is_terminal(self) -> bool:
        return self.goal_satisfied or self.step >= self.step_cap

    def to_dict(self) -> dict:
        """Schema-compatible state dict for trace emission."""
        return {
            "blocks": list(self.blocks),
            "stacks": dict(self.stacks),
            "on_table": sorted(self.on_table),
            "in_hand": self.in_hand,
            "hand_empty": self.hand_empty,
            "goal_stacks": dict(self.goal_stacks),
            "goal_on_table": sorted(self.goal_on_table),
            "goal_satisfied": self.goal_satisfied,
            "step": self.step,
            "step_cap": self.step_cap,
        }


@dataclass
class StackerAction:
    """An action submitted by the agent. `type` is one of
    pickup / putdown / stack / unstack; remaining fields depend on
    the type.
    """
    type: str
    block: str = ""
    top: str = ""
    bottom: str = ""
    from_block: str = ""

    def to_dict(self) -> dict:
        d = {"type": self.type}
        if self.block:
            d["block"] = self.block
        if self.top:
            d["top"] = self.top
        if self.bottom:
            d["bottom"] = self.bottom
        if self.from_block:
            d["from"] = self.from_block
        return d


def goal_distance(state: StackerState) -> int:
    """Number of goal predicates not currently satisfied.

    Counts (block, position) tuples in goal that don't match
    current state. Position is either "table" or "on:<bottom>".
    Range: 0 (goal satisfied) to len(blocks) (worst case).
    """
    miss = 0
    # on_table predicates
    for b in state.goal_on_table:
        if b not in state.on_table:
            miss += 1
    # stack predicates
    for bottom, top in state.goal_stacks.items():
        if state.stacks.get(bottom) != top:
            miss += 1
    return miss


class StackerEngine:
    """Stacker game engine. One instance per match (run)."""

    field_class = "structured"

    # D-076 — declarative capability requirement consumed by
    # FieldRunner gates. Stacker is a structured-class field that
    # requires JSON-emitting brains (each turn must produce a
    # parseable action envelope). Pre-D-076 a narrative-class
    # brain on Stacker hit a parse-failure cascade; the D-073
    # brain × field-class gate caught it as a class-mismatch but
    # gave a generic reason. The capability gate (D-072 surface,
    # promoted in D-076) catches it earlier and with a more
    # specific diagnosis: "field 'stacker' requires capability
    # 'json_emit', but brain declared ['narrative']".
    requires_capabilities = ["json_emit"]

    def __init__(self, instance: StackerInstance, step_cap: int = 20):
        self.instance = instance
        self.state = StackerState(
            blocks=list(instance.blocks),
            on_table=set(instance.initial_on_table),
            stacks=dict(instance.initial_stacks),
            in_hand="",
            goal_stacks=dict(instance.goal_stacks),
            goal_on_table=set(instance.goal_on_table),
            step=0,
            step_cap=step_cap,
        )

    # --- Action validation + application ---

    def validate(self, action: StackerAction) -> tuple[bool, str]:
        """Return (ok, reason). `reason` empty when ok=True."""
        s = self.state
        t = action.type
        if t == "pickup":
            b = action.block
            if not b or b not in s.blocks:
                return False, f"unknown block '{b}'"
            if not s.hand_empty:
                return False, f"hand not empty (holding {s.in_hand!r})"
            if b not in s.on_table:
                return False, f"block '{b}' not on_table"
            if not s.is_clear(b):
                return False, f"block '{b}' not clear (something on top)"
            return True, ""

        if t == "putdown":
            b = action.block
            if not b:
                return False, "missing block"
            if s.in_hand != b:
                return False, f"not holding '{b}' (in_hand={s.in_hand!r})"
            return True, ""

        if t == "stack":
            top = action.top
            bottom = action.bottom
            if not top or not bottom:
                return False, "stack requires top and bottom"
            if top == bottom:
                return False, "top and bottom must differ"
            if s.in_hand != top:
                return False, f"not holding top '{top}' (in_hand={s.in_hand!r})"
            if bottom not in s.blocks:
                return False, f"unknown bottom '{bottom}'"
            if not s.is_clear(bottom):
                return False, f"bottom '{bottom}' not clear"
            return True, ""

        if t == "unstack":
            top = action.top
            from_block = action.from_block
            if not top or not from_block:
                return False, "unstack requires top and from"
            if top == from_block:
                return False, "top and from must differ"
            if not s.hand_empty:
                return False, f"hand not empty (holding {s.in_hand!r})"
            if s.stacks.get(from_block) != top:
                return False, f"'{top}' is not on '{from_block}'"
            if not s.is_clear(top):
                return False, f"'{top}' is not clear"
            return True, ""

        return False, f"unknown action type '{t}'"

    def apply(self, action: StackerAction) -> tuple[bool, str, list[str]]:
        """Apply if valid. Returns (ok, reason, events).

        Events are human-readable strings for trace inclusion.
        """
        ok, reason = self.validate(action)
        if not ok:
            return False, reason, [f"REJECTED: {reason}"]

        s = self.state
        events: list[str] = []
        t = action.type

        prev_dist = goal_distance(s)

        if t == "pickup":
            b = action.block
            s.on_table.discard(b)
            s.in_hand = b
            events.append(f"picked up {b} from table")

        elif t == "putdown":
            b = action.block
            s.in_hand = ""
            s.on_table.add(b)
            events.append(f"put down {b} on table")

        elif t == "stack":
            top, bottom = action.top, action.bottom
            s.in_hand = ""
            s.stacks[bottom] = top
            events.append(f"stacked {top} on {bottom}")

        elif t == "unstack":
            top, from_block = action.top, action.from_block
            s.in_hand = top
            del s.stacks[from_block]
            events.append(f"unstacked {top} from {from_block}")

        s.step += 1

        # Goal predicate side-events
        new_dist = goal_distance(s)
        if new_dist < prev_dist:
            events.append(f"goal_distance: {prev_dist} → {new_dist} (closer)")
        elif new_dist > prev_dist:
            events.append(f"goal_distance: {prev_dist} → {new_dist} (further)")

        if s.goal_satisfied:
            events.append("GOAL SATISFIED")
        elif s.step >= s.step_cap:
            events.append("STEP CAP REACHED")

        return True, "", events

    # --- Reward ---

    def step_reward(self, prev_distance: int) -> float:
        """Dense per-step reward = goal_distance(prev) - goal_distance(current).

        Positive when the agent moved toward the goal.
        """
        return float(prev_distance - goal_distance(self.state))

    def terminal_reward(self) -> float:
        """+1.0 if solved within cap; 0.0 otherwise."""
        if self.state.goal_satisfied:
            return 1.0
        return 0.0

    # --- Observation ---

    def observation(self) -> dict:
        """Schema-compatible observation dict (full ground truth —
        Stacker is fully observable)."""
        return self.state.to_dict()
