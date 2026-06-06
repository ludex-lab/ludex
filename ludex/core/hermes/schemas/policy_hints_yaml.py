"""policy_hints_yaml — Hermes Interpreter schema for D-067 Phase B v3
hints extraction.

Used when a creature's distill markdown body is rich (insightful
prose) but lacks the structured YAML hints block that physis'
retrieval-filter consumes. Hermes brain reads the markdown, emits
canonical YAML hints.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Schema:
    name: str
    prompt_template: str
    validator: callable


_PROMPT = """You are translating a creature's reflection into structured policy hints. The creature wrote a free-form reflection on its accumulated experience in a `{field}` field; you extract any if-then policy patterns that creature articulated, in canonical YAML form.

The creature's reflection text:

```
{source_text}
```

Extract policy hints. Each hint must:
- Cite a precondition mappable to state features the field exposes (event, event_category, energy_band, role, phase, etc.)
- Recommend an action_type the field accepts
- Carry a confidence label per evidence count: tentative (1-2 episodes), confirmed (3-9), well-supported (10+)
- Default to tentative when evidence count is unclear or the source is reflective rather than data-cited

Output ONLY a YAML block (no preamble, no commentary, no markdown headings outside the YAML). Output an empty hints list if no extractable if-then patterns are present.

```yaml
hints:
  - id: <slug-id>
    rule: "<one-line if-then statement>"
    precondition:
      <state_feature>: <expected_value>
    action:
      type: <action_type>
    confidence: tentative | confirmed | well-supported
    evidence:
      confirmed: <N>
      disconfirmed: <N>
    last_episode: "<from-source-or-empty>"
    source: hermes_translated
```

Begin output:"""


def _validate(text: str) -> Optional[list[dict]]:
    """Pull a yaml hints list out of Hermes' translation output.

    Returns the list of hint dicts, or None on parse failure.
    """
    try:
        import yaml as _yaml
    except Exception:
        return None
    if not text:
        return None
    m = re.search(r"```yaml\s*\n(.*?)\n```", text, re.DOTALL)
    block = m.group(1) if m else text  # tolerate brain emitting plain yaml
    try:
        data = _yaml.safe_load(block)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    hints = data.get("hints")
    if hints is None:
        return None
    if not isinstance(hints, list):
        return None
    # Ensure each hint carries source tag.
    for h in hints:
        if isinstance(h, dict):
            h.setdefault("source", "hermes_translated")
    return hints


POLICY_HINTS_YAML = Schema(
    name="policy_hints_yaml",
    prompt_template=_PROMPT,
    validator=_validate,
)
