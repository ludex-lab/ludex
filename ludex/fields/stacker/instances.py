"""Stacker instances — PlanBench Blockworld 2/4/6-step problems.

Initial bundle: a small hand-curated set of canonical Blockworld
instances at three difficulty levels. The full PlanBench instance
set lives in `maitrix-org/llm-reasoners`; we'll port more in once
the baseline runs validate the format.

Each `StackerInstance` carries `optimal_steps` so we can compute
`optimal_ratio` in the trace last_line_meta. None means optimum
unknown (custom or exploratory instance).
"""
from __future__ import annotations

from .engine import StackerInstance


# 2-step problems: trivial; primarily verify the action / reward
# loop works. Optimal solver does it in 2 actions.
INST_2STEP_001 = StackerInstance(
    instance_id="planbench_2step_001",
    blocks=["A", "B"],
    initial_stacks={},
    initial_on_table=["A", "B"],
    goal_stacks={"B": "A"},
    goal_on_table=["B"],
    optimal_steps=2,
    difficulty="2-step",
    description="Goal: A on top of B. (goal_stacks={B:A} reads as 'B's top is A'.) Both start on the table.",
)

INST_2STEP_002 = StackerInstance(
    instance_id="planbench_2step_002",
    blocks=["A", "B"],
    initial_stacks={"A": "B"},
    initial_on_table=["A"],
    goal_stacks={"B": "A"},
    goal_on_table=["B"],
    optimal_steps=4,
    difficulty="2-step",
    description="Invert AB stack: B on A → A on B (requires unstack + stack via table).",
)

# 4-step problems: standard difficulty.
INST_4STEP_001 = StackerInstance(
    instance_id="planbench_4step_001",
    blocks=["A", "B", "C"],
    initial_stacks={"C": "A"},
    initial_on_table=["C", "B"],
    goal_stacks={"A": "B", "B": "C"},
    goal_on_table=["A"],
    optimal_steps=4,
    difficulty="4-step",
    description="Reorder ABC: A on top of CA → C on B on A (full inversion).",
)

INST_4STEP_002 = StackerInstance(
    instance_id="planbench_4step_002",
    blocks=["A", "B", "C"],
    initial_stacks={},
    initial_on_table=["A", "B", "C"],
    goal_stacks={"A": "B", "B": "C"},
    goal_on_table=["A"],
    optimal_steps=4,
    difficulty="4-step",
    description="Build tower with A bottom, B middle, C top. (goal_stacks={A:B,B:C} reads as 'A's top is B, B's top is C'.)",
)

# 6-step problems: harder, requires planning depth.
INST_6STEP_001 = StackerInstance(
    instance_id="planbench_6step_001",
    blocks=["A", "B", "C", "D"],
    initial_stacks={"A": "B"},
    initial_on_table=["A", "C", "D"],
    goal_stacks={"D": "C", "C": "B", "B": "A"},
    goal_on_table=["D"],
    optimal_steps=6,
    difficulty="6-step",
    description="Build 4-tall tower D-C-B-A from a partial state (B on A) plus C and D on the table.",
)


BUILTIN_INSTANCES = [
    INST_2STEP_001,
    INST_2STEP_002,
    INST_4STEP_001,
    INST_4STEP_002,
    INST_6STEP_001,
]


def load_instances(difficulty: str = "") -> list[StackerInstance]:
    """Return built-in instances, optionally filtered by difficulty.

    `difficulty` accepts "2-step" / "4-step" / "6-step" or empty for
    all. Future-compat: when llm-reasoners port lands, this function
    will dispatch to the larger instance pool.
    """
    if not difficulty:
        return list(BUILTIN_INSTANCES)
    return [i for i in BUILTIN_INSTANCES if i.difficulty == difficulty]
