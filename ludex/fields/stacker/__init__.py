"""Stacker — PlanBench-style block-stacking field (D-067 Co-MVP).

Single-agent fully-observable planning task. The agent picks up,
puts down, stacks, and unstacks labeled blocks to reach a goal
configuration. Schema: `fields/stacker/world_schema.json`.

Source instance set: maitrix-org/llm-reasoners PlanBench Blockworld
2/4/6-step problems (cherry-picked per
`docs/field-indexed-world-models-design.md` §9).
"""
from .engine import StackerEngine, StackerState, StackerAction, StackerInstance
from .instances import load_instances, BUILTIN_INSTANCES

__all__ = [
    "StackerEngine",
    "StackerState",
    "StackerAction",
    "StackerInstance",
    "load_instances",
    "BUILTIN_INSTANCES",
]
