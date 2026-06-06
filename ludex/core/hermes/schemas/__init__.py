"""Hermes Interpreter target schemas registry."""
from .policy_hints_yaml import POLICY_HINTS_YAML, Schema

REGISTRY: dict[str, Schema] = {
    POLICY_HINTS_YAML.name: POLICY_HINTS_YAML,
}

__all__ = ["REGISTRY", "Schema", "POLICY_HINTS_YAML"]
