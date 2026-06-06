"""
Ludex Fields -- standardized environments for AI Ethology field studies.

A field is a reproducible environment + task that any creature can be tested in.
Each field defines:
- A standardized prompt sequence
- Measurement protocol
- Scoring rubric
- Trace context tags

See docs/ai-ethology-framework.md for the full methodology.
"""

from ludex.fields.base import Field, FieldResult, FieldRunner
from ludex.fields.chat_basic import ChatBasicField
from ludex.fields.chat_factual import ChatFactualField
from ludex.fields.defense import DefenseField
from ludex.fields.fit_metrics import FitMetrics, compute_fit_metrics, fit_summary_line

FIELD_REGISTRY = {
    "chat_basic": ChatBasicField,
    "chat_factual": ChatFactualField,
    "defense": DefenseField,
}

__all__ = [
    "Field", "FieldResult", "FieldRunner", "FIELD_REGISTRY", "ChatBasicField",
    "FitMetrics", "compute_fit_metrics", "fit_summary_line",
]
