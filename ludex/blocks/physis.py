"""Physis — field-dynamics world-model organ (D-067 Phase A skeleton).

Physis is the creature's **"how does this field work"** sense. Where
Topos answers *where am I* and Allos answers *who else is here*,
Physis answers *what causes what in this kind of field, and what
worked before*.

Design doc: ``docs/field-indexed-world-models-design.md``.

This file is the Phase A skeleton — substrate only:

- `handle_load_world_model(field)` — read `creatures/<C>/memory/
  world_models/<field>.md` into context if it exists
- `handle_step(field, state, action, reward, next_state, **extra)` —
  append a trace entry to the in-session buffer
- `handle_consolidate(field, brain_engine=None)` — flush the
  in-session trace into `world_models/<field>.md`. The Phase A
  flush is mechanical (bare append). Phase B will wire a brain
  engine that *distills* the trace into reward correlates / policy
  hints / open uncertainty (see design doc §3.3).

Trace I/O follows the schema convention from
`docs/field-indexed-world-models-design.md` and the LxM-side
`world_schema.json` files (Avalon: lxm/avalon, Stacker: academy/
stacker). Field name is treated as a slash-namespaced path so
`world_models/lxm/avalon.md` and `world_models/academy/stacker.md`
land in nested directories.

**Storage policy (decided 2026-04-26 with Cody/JJ):**
- Live `world_models/<field>.md` is *gitignored* runtime state, in
  the same family as `memory/memories.jsonl`.
- Ethnography snapshots (D-027) include a copy of `world_models/`
  so longitudinal evolution is preserved at milestone points.
- Traces themselves (`traces/<field>/<id>/trace.jsonl`) are
  per-game-policy: LxM voxel local-only; Avalon and Stacker
  committed.

Phase A scope is intentionally narrow — get the substrate, signal
wiring, file I/O, and trace-buffer plumbing right. Brain-driven
distillation lands in Phase B once at least one field (Stacker or
Avalon) has produced traces worth distilling.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Optional

from ludex.core.block import Block
from ludex.core.port import Port

logger = logging.getLogger(__name__)


# Distill-output sanity patterns: when a brain-engine subprocess fails
# (PATH glitch, CLI binary missing, environment misconfig), the captured
# response can be an error string longer than the bare length-threshold
# but still meaningless as a world-model body. These patterns trigger
# Phase A fallback alongside the < 50 char check.
# Motivated by physis_smoke_031 ([Error: 'claude' not found from
# background-bash subprocess]) — same shape as the diagnostic we already
# log on threshold-fail (4485a5c). Match only the head of the text so
# that legitimate distillations narrating an error in body don't trip.
_DISTILL_ERROR_HEAD_CHARS = 200
_DISTILL_ERROR_PATTERNS = [
    re.compile(r"\bError:", re.IGNORECASE),
    re.compile(r"\bnot found:", re.IGNORECASE),
    re.compile(r"\bIs\s+\S+\s+installed\b", re.IGNORECASE),
]


def _looks_like_distill_error(text: str) -> bool:
    """Distill output that looks like a subprocess error string rather than
    a brain emission. Checks only the first 200 chars so multi-paragraph
    legitimate distillations narrating an error don't false-positive."""
    if not text:
        return False
    head = text[:_DISTILL_ERROR_HEAD_CHARS]
    return any(p.search(head) for p in _DISTILL_ERROR_PATTERNS)


_DISTILL_PROMPT_TEMPLATE = """You are reflecting on accumulated experience in the `{field}` task as yourself. Future-you will read this without the raw trace, so write the *lessons*, not the *log*.

Episode just played:
- outcome: `{outcome}`
- steps: {steps}
- total intermediate reward: {total_reward}

Your prior accumulated world model (or empty if first run):

```
{existing_body}
```

New episode trace (JSONL):

```
{trace_text}
```

Rewrite the world model. Output exactly three sections, in order, using these exact level-2 headers:

## Reward correlates

What state-features / patterns correlate with positive reward? What with negative? Concrete observations, not generalities. Each bullet should cite at least one episode's evidence.

## Policy hints

What action choices worked? What would future-you do differently? Phrase as falsifiable "if-then" hints. Tag each hint with one of these confidence labels — DO NOT skip:

  (tentative)        — observed in 1-2 distinct episodes
  (confirmed)        — observed in 3-9 distinct episodes with no recent disconfirmation
  (well-supported)   — observed in 10+ distinct episodes with no recent disconfirmation

If a hint's evidence dropped below threshold (e.g., a new episode disconfirmed it), demote it down a tier. Drop hints that have been refuted twice or more. NEVER mark a hint "confirmed" with fewer than 3 distinct episodes of evidence.

## Open uncertainty

What surprised you? What state-feature do you not yet have a good model for? What's worth testing next session? Be specific and short.

After the three markdown sections, append a structured YAML hint block enclosed in ```yaml fences. This block is parsed mechanically; the markdown sections are for narrative, the YAML is for retrieval. For each policy hint above, emit one entry:

```yaml
hints:
  - id: <slug-id, e.g. "shared_shelter_rest_+10">
    rule: "<one-line if-then statement, same as in markdown>"
    precondition:
      <state_feature>: <expected_value>      # e.g. event_category: cooperative
      <state_feature>: <expected_value>      # e.g. event: shared_shelter
    action:
      type: <action_type>                    # e.g. rest
    confidence: tentative | confirmed | well-supported
    evidence:
      confirmed: <N>
      disconfirmed: <N>
    last_episode: <episode_id>
```

Constraints:
- Markdown sections + YAML block, in that order. No preamble. No postscript outside the YAML fence.
- Each markdown section ≤ 300 tokens. YAML block keeps as many hints as policy section has, no more.
- Update prior body and prior hints — don't just append. Promote stable patterns, demote / drop refuted ones, add new tentatives.

Begin output:"""


@dataclass
class TraceStep:
    """One observation in the in-session trace buffer.

    Mirrors the per-line schema from world_schema.json's
    `trace_format.per_line_schema` so the consolidation pass can
    serialize without translation.
    """
    turn: int
    phase: str = ""
    active_agent_id: str = ""
    ground_truth_state: dict = dc_field(default_factory=dict)
    agent_views: dict = dc_field(default_factory=dict)
    action: dict = dc_field(default_factory=dict)
    reward: float = 0.0
    reward_per_agent: dict = dc_field(default_factory=dict)
    self_eval: str = ""
    events: list = dc_field(default_factory=list)


@dataclass
class WorldModelHandle:
    """Per-field, per-creature world-model file location + cached body."""
    field: str
    path: Path
    body: str = ""
    loaded_at: float = 0.0


class PhysisBlock(Block):
    """Field-dynamics world-model organ.

    provides:
        load_world_model — load world_models/<field>.md into context
        step             — append a step to the in-session trace buffer
        consolidate      — flush the trace into world_models/<field>.md
        clear_trace      — reset the in-session buffer

    requires: (none — reads habitat dir from organism config)

    D-067 Phase A skeleton.
    """

    name = "physis"
    provides = [
        Port("load_world_model", description="Read world_models/<field>.md into context if it exists"),
        Port("step", description="Append a step to the in-session trace buffer"),
        Port("consolidate", description="Flush in-session trace into world_models/<field>.md"),
        Port("clear_trace", description="Reset the in-session trace buffer"),
        Port("get_relevant_hints", description="Phase B v3 — retrieval-filtered hints for current state signature"),
    ]
    requires = []

    def __init__(self):
        super().__init__()
        self._trace_buffer: list[TraceStep] = []
        self._current_field: str = ""
        self._handles: dict[str, WorldModelHandle] = {}

    # --- Lifecycle ---

    def on_attach(self) -> None:
        # Phase A doesn't subscribe to any turn-end signal yet — fields
        # call step()/consolidate() explicitly through the port wiring.
        # Phase B will add a "field.tick.ended" subscription so that
        # fields with native ticks emit traces without needing the field
        # author to plumb physis manually.
        pass

    # --- Internal helpers ---

    def _habitat_dir(self) -> Optional[Path]:
        cfg = self._config
        if cfg is None:
            return None
        habitat_dir = cfg.get("habitat_dir", "") if hasattr(cfg, "get") else ""
        if not habitat_dir:
            return None
        return Path(habitat_dir)

    def _world_model_path(self, field: str) -> Optional[Path]:
        habitat = self._habitat_dir()
        if habitat is None:
            return None
        # Slash-namespaced field becomes nested dir.
        parts = field.split("/") if field else ["unknown"]
        wm_path = habitat / "memory" / "world_models" / Path(*parts)
        wm_path = wm_path.with_suffix(".md")
        return wm_path

    # --- Provides: load_world_model ---

    def handle_load_world_model(self, field: str) -> str:
        """Load existing world_models/<field>.md body if present.

        Returns the body string (empty if no file exists). Caches the
        handle so subsequent calls are cheap.
        """
        if not field:
            return ""
        self._current_field = field

        path = self._world_model_path(field)
        if path is None:
            logger.debug("physis.load_world_model: no habitat_dir, returning empty")
            return ""

        if path.exists():
            try:
                body = path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"physis.load_world_model: read failed for {path}: {e}")
                body = ""
        else:
            body = ""

        self._handles[field] = WorldModelHandle(
            field=field, path=path, body=body, loaded_at=time.time(),
        )
        return body

    # --- Provides: step ---

    def handle_step(
        self,
        field: str,
        turn: int,
        ground_truth_state: Optional[dict] = None,
        action: Optional[dict] = None,
        reward: float = 0.0,
        *,
        phase: str = "",
        active_agent_id: str = "",
        agent_views: Optional[dict] = None,
        reward_per_agent: Optional[dict] = None,
        self_eval: str = "",
        events: Optional[list] = None,
    ) -> None:
        """Append a step to the in-session trace buffer.

        Field, turn, ground_truth_state are the load-bearing minimal
        triple. Other kwargs match the world_schema.json
        trace_format.per_line_schema; passing them through as-is means
        the consolidation pass can emit schema-compatible JSONL.
        """
        if field and field != self._current_field:
            # New field — clear buffer (different episode).
            self._trace_buffer.clear()
            self._current_field = field

        step = TraceStep(
            turn=turn,
            phase=phase,
            active_agent_id=active_agent_id,
            ground_truth_state=dict(ground_truth_state or {}),
            agent_views=dict(agent_views or {}),
            action=dict(action or {}),
            reward=float(reward),
            reward_per_agent=dict(reward_per_agent or {}),
            self_eval=self_eval,
            events=list(events or []),
        )
        self._trace_buffer.append(step)

    # --- Provides: consolidate ---

    def handle_consolidate(
        self,
        field: str = "",
        brain_engine: Any = None,
        episode_id: str = "",
        outcome: str = "",
        prompt_addendum: str = "",
    ) -> str:
        """Flush the in-session trace into world_models/<field>.md.

        - Phase A (`brain_engine=None`): append episode header + bare
          JSONL trace. No brain call.
        - Phase B (`brain_engine` provided): brain re-distills across
          (existing world_model + new episode trace) into structured
          sections (reward correlates, policy hints, open uncertainty).
          Replaces the file body each call.

        Returns the path written (or empty string if nothing to do).
        """
        f = field or self._current_field
        if not f:
            logger.debug("physis.consolidate: no field provided, skipping")
            return ""
        if not self._trace_buffer:
            logger.debug(f"physis.consolidate: empty trace buffer for {f}, skipping")
            return ""

        path = self._world_model_path(f)
        if path is None:
            logger.warning("physis.consolidate: no habitat_dir, cannot write")
            return ""

        path.parent.mkdir(parents=True, exist_ok=True)

        # Phase B dispatch: when a brain_engine is provided, do
        # brain-driven distillation. Falls through to Phase A on any
        # error (best-effort distillation, never blocks the trace
        # accumulation).
        if brain_engine is not None:
            try:
                return self._phase_b_consolidate(
                    field=f, brain_engine=brain_engine, path=path,
                    episode_id=episode_id, outcome=outcome,
                    prompt_addendum=prompt_addendum,
                )
            except Exception as e:
                logger.warning(
                    f"physis.consolidate Phase B failed ({e}); "
                    f"falling back to Phase A append"
                )

        n = len(self._trace_buffer)
        total_reward = sum(s.reward for s in self._trace_buffer)
        creature_name = getattr(self._organism, "name", "") if self._organism else ""
        brain_str = ""
        cfg = self._config
        if cfg is not None and hasattr(cfg, "get"):
            # Organism's runtime config exposes "model" / "provider" at top
            # level (see organism_config.build → Organism.config dict).
            model = cfg.get("model", "")
            provider = cfg.get("provider", "")
            if model:
                brain_str = f"{model} ({provider})" if provider else model

        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        header_lines = []
        if not path.exists():
            header_lines.extend([
                "---",
                f"field: {f}",
                f"creature: {creature_name}",
                f"brain: {brain_str}",
                "episodes_observed: 0",
                f"last_updated: {ts}",
                "confidence: 0.0",
                "---",
                "",
                f"# World model — `{f}`",
                "",
                ("> Phase A skeleton. Trace appended verbatim per episode. "
                 "Phase B will replace this with brain-distilled reward "
                 "correlates, policy hints, and open uncertainty (per "
                 "`docs/field-indexed-world-models-design.md` §3.2)."),
                "",
            ])

        episode_lines = [
            "",
            "---",
            "",
            f"## Episode `{episode_id or '?'}`  ({ts})",
            "",
            f"- outcome: `{outcome or '?'}`",
            f"- steps: {n}",
            f"- total_reward: {total_reward:.3f}",
            "",
            "<details>",
            "<summary>Trace (JSON per line)</summary>",
            "",
            "```jsonl",
        ]
        for s in self._trace_buffer:
            line = {
                "turn": s.turn,
                "phase": s.phase,
                "active_agent_id": s.active_agent_id,
                "ground_truth_state": s.ground_truth_state,
                "agent_views": s.agent_views,
                "action": s.action,
                "reward": s.reward,
                "reward_per_agent": s.reward_per_agent,
                "self_eval": s.self_eval,
                "events": s.events,
            }
            episode_lines.append(json.dumps(line, ensure_ascii=False))
        episode_lines.extend([
            "```",
            "",
            "</details>",
            "",
        ])

        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        new_body = existing + ("\n".join(header_lines) if header_lines else "") + "\n".join(episode_lines)
        path.write_text(new_body, encoding="utf-8")

        logger.info(
            f"physis.consolidate: wrote {n} steps (total_reward={total_reward:.3f}) "
            f"to {path}"
        )

        # Reset buffer; episode is closed.
        self._trace_buffer.clear()
        # brain_engine is accepted for forward-compat; Phase A does not use it.
        _ = brain_engine
        return str(path)

    # --- Phase B: brain-distilled consolidation ---

    def _phase_b_consolidate(
        self,
        field: str,
        brain_engine: Any,
        path: Path,
        episode_id: str,
        outcome: str,
        prompt_addendum: str = "",
    ) -> str:
        """Brain-distilled consolidation. Re-distills the existing
        world model body together with the new episode trace into
        a fresh structured body.

        Output sections (per `docs/physis-phase-b-design.md` §3.2):
          ## Reward correlates
          ## Policy hints
          ## Open uncertainty

        On distillation failure (parse, brain refused, etc.) raises;
        the caller falls back to Phase A.
        """
        existing_body = path.read_text(encoding="utf-8") if path.exists() else ""
        # Trim existing body of any prior frontmatter / headers we own;
        # the brain only needs the *content* sections to roll forward.
        existing_content = self._strip_world_model_header(existing_body)

        n = len(self._trace_buffer)
        total_reward = sum(s.reward for s in self._trace_buffer)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        creature_name = getattr(self._organism, "name", "") if self._organism else ""

        # Compact trace JSONL for the prompt.
        trace_lines = []
        for s in self._trace_buffer:
            trace_lines.append(json.dumps({
                "turn": s.turn,
                "phase": s.phase,
                "ground_truth_state": s.ground_truth_state,
                "action": s.action,
                "reward": s.reward,
                "events": s.events,
            }, ensure_ascii=False))
        trace_text = "\n".join(trace_lines)

        prompt = _DISTILL_PROMPT_TEMPLATE.format(
            field=field,
            outcome=outcome or "?",
            steps=n,
            total_reward=f"{total_reward:.3f}",
            existing_body=(existing_content[-3500:] if existing_content else "(no prior model)"),
            trace_text=trace_text[:4500],
        )

        # Field-specific calibration: per-field schemas (LxM Avalon's
        # `world_schema.json` etc.) push a `distill_prompt_addendum`
        # string the field author owns. We inject it directly above the
        # "Rewrite the world model." section so it constrains the
        # brain's emission without crossing the D-052 sovereignty line
        # (Ludex doesn't read LxM file paths; LxM hands the string in).
        if prompt_addendum:
            prompt = prompt.replace(
                "Rewrite the world model.",
                f"Field-specific calibration:\n\n{prompt_addendum}\n\nRewrite the world model.",
                1,
            )

        # Use bypass_memory so the creature's biographical recall
        # doesn't bleed into the distillation prompt.
        result = brain_engine.handle_submit(prompt, bypass_memory=True)
        distilled_text = (getattr(result, "response", "") or "").strip()

        if not distilled_text or len(distilled_text) < 50:
            logger.warning("physis distill below threshold; raw text (%d chars): %r", len(distilled_text), distilled_text)
            raise ValueError(
                f"distilled output too short ({len(distilled_text)} chars) — "
                "fall back to Phase A"
            )

        if _looks_like_distill_error(distilled_text):
            logger.warning("physis distill looks like subprocess error; raw text (%d chars): %r", len(distilled_text), distilled_text)
            raise ValueError(
                "distilled output matches subprocess-error pattern — "
                "fall back to Phase A"
            )

        brain_str = self._brain_str()
        # Rebuild the file with fresh frontmatter + distilled sections.
        # Episode metadata is preserved as a rolling list at the bottom.
        episode_summary = (
            f"- `{episode_id or '?'}` ({ts}) — {outcome or '?'}, "
            f"{n} step(s), reward={total_reward:.3f}"
        )
        new_body = "\n".join([
            "---",
            f"field: {field}",
            f"creature: {creature_name}",
            f"brain: {brain_str}",
            f"last_updated: {ts}",
            f"phase: B",
            "---",
            "",
            f"# World model — `{field}`",
            "",
            distilled_text.rstrip(),
            "",
            "## Recent episodes (rolling)",
            "",
            episode_summary,
            self._extract_prior_episode_lines(existing_content, max_keep=9),
            "",
        ])
        path.write_text(new_body, encoding="utf-8")
        logger.info(
            f"physis.consolidate Phase B: distilled {n} steps "
            f"(total_reward={total_reward:.3f}) into {path}"
        )

        # Phase B v3: extract structured hints from the YAML block the
        # distill prompt requests. Save to hints.yaml sibling for
        # retrieval-filtered injection. Best-effort — Phase B markdown
        # body is what carries narrative; hints.yaml is the
        # retrieval substrate.
        try:
            self._save_hints_from_distill(field, distilled_text, episode_id)
        except Exception as e:
            logger.warning(f"physis.consolidate Phase B v3 hints extraction failed: {e}")

        self._trace_buffer.clear()
        return str(path)

    @staticmethod
    def _strip_world_model_header(body: str) -> str:
        """Drop our own frontmatter + title block so the brain only
        sees content sections from prior runs."""
        if not body:
            return ""
        # Strip leading frontmatter (--- ... ---)
        if body.lstrip().startswith("---"):
            parts = body.split("---", 2)
            if len(parts) >= 3:
                body = parts[2].lstrip()
        # Drop the H1 title if present
        lines = body.splitlines()
        kept = []
        skip_blank = False
        for ln in lines:
            if not kept and ln.startswith("# World model"):
                skip_blank = True
                continue
            if skip_blank and not ln.strip():
                skip_blank = False
                continue
            kept.append(ln)
        return "\n".join(kept).strip()

    @staticmethod
    def _extract_prior_episode_lines(body: str, max_keep: int = 9) -> str:
        """Pull '- `episode_id` (ts) — outcome, N step(s), reward=X.YYY'
        bullets from a prior body's Recent episodes section."""
        if not body:
            return ""
        if "## Recent episodes" not in body:
            return ""
        section = body.split("## Recent episodes", 1)[1]
        bullets = []
        for line in section.splitlines():
            ls = line.strip()
            if ls.startswith("- "):
                bullets.append(ls)
            elif bullets and not ls:
                continue
            elif bullets:
                break
        return "\n".join(bullets[:max_keep])

    # --- Phase B v3: structured hints (retrieval-filtered injection) ---

    def _hints_path(self, field: str) -> Optional[Path]:
        """Sibling YAML to world_models/<field>.md, gitignored runtime."""
        wm_path = self._world_model_path(field)
        if wm_path is None:
            return None
        return wm_path.with_suffix(".hints.yaml")

    def _save_hints_from_distill(self, field: str, distilled_text: str, episode_id: str) -> None:
        """Extract the ```yaml ... ``` block from the distill output and
        save to hints sidecar. Brain is asked to emit this block per
        the distill prompt; missing block triggers D-069 narrative-
        extractor fallback that scans the `## Policy hints` markdown
        section for bullet-form `if event=X, ..., action=Y` patterns.

        v3 architectural gap fix (commit 4c810fe finding): brains that
        skip YAML emission (haiku/sonnet/gemini-pro) still get
        retrieval-filtered hints when their narrative carries
        if-then structure.
        """
        try:
            import yaml as _yaml
        except Exception:
            logger.debug("PyYAML not available; skipping hints save")
            return

        hints = []
        # Tier 1: YAML block (function-calling-trained brains).
        m = re.search(r"```yaml\s*\n(.*?)\n```", distilled_text, re.DOTALL)
        if m:
            try:
                data = _yaml.safe_load(m.group(1))
                if isinstance(data, dict):
                    raw = data.get("hints") or []
                    if isinstance(raw, list):
                        hints = raw
            except Exception as e:
                logger.warning(f"Failed to parse hints yaml block: {e}")

        # Tier 2: D-069 narrative extractor (prose-trained brains).
        # When tier 1 yielded no hints, scan the `## Policy hints`
        # markdown section for bullet lines that already follow
        # if-then form. Brains like Anvil emit both YAML and narrative;
        # brains like Hearth emit only narrative; brains like Loom
        # emit YAML only. The extractor only fires when YAML absent.
        if not hints:
            extracted = self._extract_hints_from_markdown(
                distilled_text, episode_id
            )
            if extracted:
                logger.info(
                    f"physis D-069 narrative-extractor: pulled "
                    f"{len(extracted)} hint(s) from markdown body "
                    f"(YAML block was absent)"
                )
                hints = extracted

        # Tier 3: D-070 Hermes Interpreter (reflective prose, no
        # if-then surface form). Some brains write rich
        # reflection that the narrative regex misses. Hermes
        # asks a coordinator brain (Opus / Codex 5.5 / Gemini 3.1 Pro)
        # to translate the prose into canonical YAML.
        if not hints:
            try:
                from ludex.core.hermes import hermes
                translated = hermes.interpreter.translate(
                    source_text=distilled_text,
                    target_schema="policy_hints_yaml",
                    field=field,
                )
            except Exception as e:
                logger.warning(f"Hermes Interpreter call failed: {e}")
                translated = None
            if translated:
                logger.info(
                    f"physis D-070 Hermes Interpreter: translated "
                    f"{len(translated)} hint(s) from reflective prose"
                )
                # Stamp episode_id when the translator left it empty.
                for h in translated:
                    if isinstance(h, dict) and not h.get("last_episode"):
                        h["last_episode"] = episode_id
                hints = translated

        if not hints:
            logger.debug("No hints extracted via YAML, narrative, or Hermes tier")
            return

        # Phase B v3 confidence post-processing (D-067, 2026-04-27).
        # Brains (especially SLMs) don't reliably enforce the
        # n<3 → tentative / n<10 → confirmed rule from the distill
        # prompt. We re-validate against evidence.confirmed count and
        # silently downgrade. Log for transparency.
        downgrade_count = 0
        for h in hints:
            if not isinstance(h, dict):
                continue
            ev = h.get("evidence") or {}
            confirmed_n = ev.get("confirmed", 0) if isinstance(ev, dict) else 0
            current = (h.get("confidence") or "tentative").strip().lower()
            corrected = current
            if confirmed_n < 3 and current in ("confirmed", "well-supported", "well_supported"):
                corrected = "tentative"
            elif confirmed_n < 10 and current in ("well-supported", "well_supported"):
                corrected = "confirmed"
            if corrected != current:
                h["confidence"] = corrected
                downgrade_count += 1
        if downgrade_count:
            logger.info(
                f"physis confidence post-processing: downgraded "
                f"{downgrade_count}/{len(hints)} hint(s) to match evidence counts"
            )

        path = self._hints_path(field)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # Stamp episode for trace; write fresh each consolidate (the
        # brain is meant to update prior hints, not append).
        out = {
            "field": field,
            "last_episode": episode_id,
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "hints": hints,
        }
        path.write_text(_yaml.safe_dump(out, sort_keys=False, allow_unicode=True), encoding="utf-8")
        logger.info(f"physis: saved {len(hints)} hint(s) to {path}")

    @staticmethod
    def _extract_hints_from_markdown(distilled_text: str, episode_id: str) -> list[dict]:
        """D-069 narrative-extractor: pull hints from `## Policy hints`
        markdown section when YAML emit was skipped.

        Looks for bullet lines that contain "if ... then ..." structure
        with extractable preconditions (event, event_category,
        energy_band, etc.) and an action verb. Tentative confidence
        only — narrative-extracted hints don't carry the brain's
        own confidence calibration.

        Per-field cue keywords are Wilderness-leaning today; can be
        parameterized per field when more fields adopt physis.
        """
        if "## Policy hints" not in distilled_text:
            return []
        # Slice the Policy hints section.
        start = distilled_text.index("## Policy hints")
        # End at next h2 or end-of-text.
        rest = distilled_text[start + len("## Policy hints"):]
        next_h2 = re.search(r"\n##\s+\w", rest)
        section = rest[: next_h2.start()] if next_h2 else rest

        # Look for bullets starting with "- ". For each, try to extract
        # event / event_category / energy_band / action.
        action_words = (
            "rest", "explore", "defend", "support", "speak",
            "pickup", "putdown", "stack", "unstack",
            "vote", "propose", "sabotage",
        )
        hints = []
        bullet_re = re.compile(r"^\s*-\s+(.+?)$", re.MULTILINE)
        for bm in bullet_re.finditer(section):
            line = bm.group(1).strip()
            line_lower = line.lower()
            if "if" not in line_lower or "then" not in line_lower and "prefer" not in line_lower and "avoid" not in line_lower:
                continue
            # Extract event / event_category / energy_band patterns.
            precondition = {}
            ev = re.search(r"event\s*[=:]\s*[`'\"]?([a-z_]+)[`'\"]?", line_lower)
            if ev:
                precondition["event"] = ev.group(1)
            cat = re.search(r"event_category\s*[=:]\s*[`'\"]?([a-z_]+)[`'\"]?", line_lower)
            if cat:
                precondition["event_category"] = cat.group(1)
            energy = re.search(r"energy[_\s-]*band\s*[=:]\s*[`'\"]?(high|medium|low)[`'\"]?", line_lower)
            if energy:
                precondition["energy_band"] = energy.group(1)

            # Extract action — the first action verb mentioned after
            # "prefer" / "choose" / "then" / "avoid".
            action_type = ""
            verb_kind = ""
            for kw in ("prefer", "choose", "then", "avoid"):
                if kw in line_lower:
                    after = line_lower.split(kw, 1)[1]
                    for action in action_words:
                        am = re.search(rf"\b{action}\b", after)
                        if am:
                            action_type = action
                            verb_kind = kw
                            break
                    if action_type:
                        break

            if not precondition or not action_type:
                continue

            # If "avoid X", store as an avoidance hint (not a positive
            # prefer-X hint). For simplicity, encode avoidance with a
            # different action key. Downstream retrieval cares about
            # precondition match; the rule string carries the verb.
            slug_action = ("avoid_" + action_type) if verb_kind == "avoid" else action_type
            slug = f"narr_{slug_action}_{'_'.join(precondition.values())[:40]}"
            hints.append({
                "id": slug,
                "rule": line,
                "precondition": precondition,
                "action": {"type": slug_action},
                "confidence": "tentative",
                "evidence": {"confirmed": 1, "disconfirmed": 0},
                "last_episode": episode_id,
                "source": "narrative_extracted",
            })
        return hints

    def handle_get_relevant_hints(self, field: str, state_signature: dict, max_hints: int = 5) -> list[dict]:
        """Phase B v3 retrieval-filter: return hints whose precondition
        is consistent with the current state.

        A hint is relevant when every key in `precondition` either
        matches `state_signature` or isn't in `state_signature` at all
        (we don't filter on missing features — caller's signature is
        the authority).

        `max_hints` cap prevents prompt bloat. Hints with higher
        confidence sort first.

        Returns list of hint dicts; empty if no file or no matches.
        """
        try:
            import yaml as _yaml
        except Exception:
            return []
        path = self._hints_path(field)
        if path is None or not path.exists():
            return []
        try:
            data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return []
        hints = data.get("hints") or []
        if not isinstance(hints, list):
            return []

        confidence_rank = {"well-supported": 3, "confirmed": 2, "tentative": 1}
        matched = []
        for h in hints:
            if not isinstance(h, dict):
                continue
            pc = h.get("precondition") or {}
            if not isinstance(pc, dict):
                continue
            ok = True
            for k, v in pc.items():
                if k in state_signature and state_signature[k] != v:
                    ok = False
                    break
            if ok:
                matched.append(h)
        # Sort: higher confidence first; within same tier, more evidence first.
        matched.sort(
            key=lambda h: (
                -confidence_rank.get(h.get("confidence") or "tentative", 1),
                -(h.get("evidence", {}).get("confirmed") or 0),
            )
        )
        return matched[:max_hints]

    def _brain_str(self) -> str:
        cfg = self._config
        if cfg is None or not hasattr(cfg, "get"):
            return ""
        model = cfg.get("model", "")
        provider = cfg.get("provider", "")
        if model:
            return f"{model} ({provider})" if provider else model
        return ""

    # --- Provides: clear_trace ---

    def handle_clear_trace(self) -> int:
        """Reset the in-session buffer without writing. Returns prior length."""
        n = len(self._trace_buffer)
        self._trace_buffer.clear()
        return n

    # --- Introspection ---

    def trace_buffer_size(self) -> int:
        return len(self._trace_buffer)
