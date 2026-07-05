"""
Creature Selfhood — SELF.md + Bonds + Reflection

D-021: SELF.md emerges from reflection, not configuration.
D-022: Bonds are structured relationship records that grow from experience.
D-023: Theory of Mind — bonds carry mental models; predictions are verified
       against observation, building social intelligence over time.

Inspired by gyeol (inureyes/gyeol), adapted for Ludex multi-creature model.

Usage:
    from ludex.core.selfhood import reflect, update_bond, load_self, load_bonds

    # After a Wilderness or Agora session
    reflect(organism, trigger="wilderness_complete")

    # After meeting another creature
    update_bond(organism, other_name="Spark", shared_experience="...")
"""

from __future__ import annotations

import json
import os
import re
import time
import logging
from pathlib import Path
from typing import Any

from ludex.core.memory_types import IMPORTANCE_REFLECTION, IMPORTANCE_SIGNIFICANCE_LINE

logger = logging.getLogger(__name__)


# ============================================================
# SELF.md — emergent self-understanding
# ============================================================

def load_self(habitat_dir: str) -> str:
    """Read the creature's current SELF.md. Returns content or empty string."""
    path = Path(habitat_dir) / "SELF.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


# D-085 param 3 — the identity block is the one region of SELF.md that
# reflect()'s full-rewrite must NOT clobber: it holds [PROVISIONAL] and
# [SETTLED] identity observations managed by the consolidation promotion
# gate, not by individual reflections.
IDENTITY_BLOCK_START = "<!-- ludex:identity:start -->"
IDENTITY_BLOCK_END = "<!-- ludex:identity:end -->"


def extract_identity_block(text: str) -> str:
    """Return the marker-delimited identity block (markers included), or ""."""
    start = text.find(IDENTITY_BLOCK_START)
    if start == -1:
        return ""
    end = text.find(IDENTITY_BLOCK_END, start)
    if end == -1:
        return ""
    return text[start:end + len(IDENTITY_BLOCK_END)]


def load_self_compressed(habitat_dir: str, max_chars: int = 200) -> str:
    """Load SELF.md compressed for prompt injection.

    Extracts key lines (behavioral patterns, lessons) and compresses
    to a short block suitable for system prompt. `max_chars` scales
    with the brain tier (prompt_tier.injection_budget): a large brain
    can carry a fuller self; a small one gets the essence.
    """
    content = load_self(habitat_dir)
    if not content or "empty at birth" in content:
        return ""

    # Strip XML tags that some brains (Gemini) wrap around content
    import re
    content = re.sub(r'<[^>]+>', '', content)
    # Strip markdown bold markers
    content = content.replace("**", "")

    # Extract meaningful lines (skip headers, empty lines, meta)
    lines = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("*") and stripped.endswith("*"):
            continue  # skip italic-only lines (e.g., *settles into reflection*)
        if stripped.startswith("- "):
            lines.append(stripped[2:])
        elif stripped.startswith("Last reflection"):
            continue
        else:
            lines.append(stripped)

    if not lines:
        return ""

    # Compress to the caller's budget (default ~200 chars)
    compressed = "; ".join(lines)
    if len(compressed) > max_chars:
        compressed = compressed[:max_chars] + "..."
    return f"[Self] {compressed}"


def reflect(organism, trigger: str = "manual", engine=None,
            session_context: str = "", hiatus_marker=None) -> str:
    """Trigger a reflection cycle for the creature.

    The creature examines its recent memories and produces a self-assessment.
    The result is written to SELF.md in the habitat.

    Args:
        organism: The Organism instance
        trigger: What caused this reflection (wilderness_complete, agora_complete, periodic, manual)
        engine: EngineBlock to use (auto-detected if None)
        session_context: Optional ground-truth summary of the just-finished
            session — pre-formatted prose that goes into the prompt verbatim,
            ahead of the fuzzy memory recall block. Use this when the caller
            (e.g. Wilderness, Council) has authoritative session metadata
            that fuzzy recall over many prior sessions can drown out.
            Empty string means "skip this block; rely on memory recall."
        hiatus_marker: Optional HiatusMarker. When the wake is the first
            after a caretaker-declared dormancy, the parsed marker is
            spliced into the prompt header so the reflection can
            acknowledge the gap rather than narrate as if time were
            continuous. See ludex.core.hiatus.

    Returns:
        The reflection text, or empty string on failure.
    """
    config = getattr(organism, "config", None)
    if not config:
        return ""
    habitat_dir = config.get("habitat_dir", "") if hasattr(config, "get") else ""
    if not habitat_dir:
        return ""

    if engine is None:
        engine = organism.get_block("engine")
    if engine is None:
        return ""

    memory = organism.get_block("memory")

    # Gather recent memories for reflection material.
    # Collect Memory objects (not strings) so disclose_memories can
    # pick the right detail level per brain tier (D-024 Sprint 2).
    recent_memory_objs = []
    seen_ids = set()
    if memory:
        try:
            stats = memory.stats()
            for query in ["wilderness", "agora", "important", "learned"]:
                results = memory.handle_recall(query, limit=5)
                for r in results:
                    mem_obj = r.memory if hasattr(r, "memory") else None
                    if mem_obj is None or mem_obj.id in seen_ids:
                        continue
                    seen_ids.add(mem_obj.id)
                    recent_memory_objs.append(mem_obj)
        except Exception as e:
            logger.debug(f"Reflection: memory recall failed: {e}")

    # Read current SELF.md
    current_self = load_self(habitat_dir)

    # Read bonds
    bonds_summary = _summarize_bonds(habitat_dir)

    # D-024 Sprint 2: progressive disclosure — memory text density
    # matched to brain tier (SMALL_SLM=L1 count+tags, MID_SLM/
    # LARGE_SLM=L2 headlines, MID/LARGE=L3 full truncated).
    from ludex.core.memory_types import disclose_memories, disclosure_level_for
    brain_for_disclosure = config.get("brain", {}) if hasattr(config, "get") else {}
    memory_text = disclose_memories(
        recent_memory_objs[:15],
        level=disclosure_level_for(brain_for_disclosure),
    ) if recent_memory_objs else ""
    header = f"[Reflection — triggered by: {trigger}]\n\n"
    if hiatus_marker is not None:
        from ludex.core.hiatus import build_reflect_context
        header += build_reflect_context(hiatus_marker) + "\n\n"
    # Authoritative session context goes FIRST (after hiatus context if any),
    # before the fuzzy recall block. Fixes the recall-drowning failure
    # mode where a creature with many prior sessions retrieves older
    # memories that score higher than today's specific tick records,
    # leaving the brain to invent the just-finished session's shape.
    # (Verified 2026-05-09: Quill's reflection misremembered an 8-tick
    # support-heavy wilderness as "two ticks weathered, rested through
    # both, energy untouched" — the actual session memories existed in
    # memories.jsonl but were ranked below 56 prior wilderness memories.)
    if session_context:
        header += f"This session (authoritative):\n{session_context}\n\n"
    if memory_text:
        header += f"Your recent memories:\n{memory_text}\n\n"
    # D-085 param 4 — dual-thread invariant: when consolidation
    # retrospectives exist, the reflection prompt carries the consolidated
    # hindsight ALONGSIDE the raw recent memories, so the creature faces
    # the contrast between in-the-moment record and synthesized hindsight.
    # Degrades silently for creatures that have never consolidated.
    if memory and hasattr(memory, "recall_consolidated"):
        try:
            cons = memory.recall_consolidated("identity pattern learned held shifted", limit=2)
            if cons:
                cons_text = "\n\n".join(
                    f"({c['file']} — {c['section']})\n{c['text'][:800]}"
                    for c in cons)
                header += ("Your consolidated retrospectives "
                           f"(hindsight, distinct from the raw record):\n{cons_text}\n\n")
        except Exception as e:
            logger.debug(f"Reflection: consolidated recall failed: {e}")
    if bonds_summary:
        header += f"Your relationships:\n{bonds_summary}\n\n"
    if current_self and "empty at birth" not in current_self:
        header += f"Your previous self-understanding:\n{current_self}\n\n"

    from ludex.core.prompt_tier import build_tiered, tier_of
    brain = brain_for_disclosure
    target = tier_of(brain)
    # B (gyeol-aligned, 2026-06-16): the self-portrait EVOLVES in place
    # rather than being rewritten from scratch each cycle. Full-rewrite was
    # the cause of the "fragmentary / nothing accumulates" feeling — every
    # reflection reset the specifics. gyeol (our SELF.md's origin) keeps
    # stable sections that accumulate; the literature warns that *growing*
    # the canonical self (compress+append) invites summary drift, so we do
    # NOT grow it — we carry forward into bounded, stable sections. History
    # proper lives in the queryable archive (snapshots + memory), not here.
    tiered = build_tiered(
        essential=(
            "Below is your standing self-understanding. EVOLVE it — do not "
            "start over. Keep what is still true, revise what has changed, and "
            "fold in what your recent experience revealed. In your own voice; "
            "this is self-examination, not a task report."
        ),
        task=(
            "Keep three standing sections, updating each in place:\n"
            "## Patterns — what you tend to do under stress, in calm, with others\n"
            "## Lessons — what you have actually learned from experience\n"
            "## Open questions — what about yourself you have not answered yet\n"
            "Carry forward the specific, hard-won details already in your "
            "previous self-understanding; do not dissolve them into vague "
            "generalities. Add what is new; drop only what is no longer true "
            "of you."
        ),
        elaboration=(
            "Write only what you have actually observed about yourself. Do not "
            "aspire; do not generalize away specifics. If a section is "
            "genuinely empty for you, keep its header with one honest line."
        ),
        constraints_negative=[
            "Do not refer to SELF.md, saving, updating, or any writing action.",
            "Do not write 'I have reflected' / 'I've updated' / '... is now recorded' or any meta-description.",
            "No XML tags, no code blocks. Use ONLY the three '## ' section headers given above; first-person prose under each.",
            "Output ONLY the self-understanding itself; it will be saved verbatim.",
        ],
        length="A few lines under each section — the whole portrait stays compact (under ~20 lines).",
        frame=(
            "Your entire response replaces the previous version verbatim as "
            "your standing self-understanding — so it must carry forward "
            "everything still true of you, not only this latest moment."
        ),
        target=target,
    )
    prompt = header + tiered.prompt

    # Get reflection from the creature's brain
    try:
        result = engine.handle_submit(prompt)
        reflection_text = result.response or ""
    except Exception as e:
        logger.error(f"Reflection failed: {e}")
        return ""

    try:
        from ludex.core import trace as _tr
        _tr.emit_translation_applied(organism, tiered)
    except Exception:
        pass

    if not reflection_text:
        return ""

    # Validate: reject meta-reports ("I have reflected..." style) and adapter
    # error-fallbacks ('[Error: ...]'). Preserve existing SELF.md rather than
    # overwrite with a degraded response.
    if _looks_like_meta_report(reflection_text) or _is_error_fallback(reflection_text):
        logger.warning(
            f"Reflection for {organism.name} looked like a meta-report; "
            f"preserving existing SELF.md. Raw response (first 200 chars): "
            f"{reflection_text[:200]!r}"
        )
        return ""

    # Write to SELF.md
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    self_content = (
        f"# {organism.name} — Self-Understanding\n"
        f"Last reflection: {now} (trigger: {trigger})\n\n"
        f"{reflection_text}\n"
    )

    # D-085 param 3: the rewrite preserves the promotion-gate identity
    # block (settled/provisional observations) verbatim — reflections may
    # not erase what the two-window gate settled.
    identity_block = extract_identity_block(current_self)
    if identity_block:
        self_content += f"\n{identity_block}\n"

    self_path = Path(habitat_dir) / "SELF.md"
    try:
        self_path.write_text(self_content, encoding="utf-8")
        logger.info(f"SELF.md updated for {organism.name} ({len(reflection_text)} chars)")
    except Exception as e:
        logger.error(f"Failed to write SELF.md: {e}")

    # Store reflection as identity memory (D-024: identity type,
    # cold tier, 10× decay multiplier — never archived by dream cycle)
    if memory:
        try:
            memory.handle_remember(
                f"Self-reflection ({trigger}): {reflection_text[:300]}",
                memory_type="identity",
                tags=["reflection", "self", trigger],
                source=f"reflection/{trigger}",
                importance=IMPORTANCE_REFLECTION,
            )
        except Exception:
            pass

    # ① Durable event memory (D-044 archive principle): also store the
    # OBJECTIVE event as an episodic memory. The reflection above is the brain's *interpretation*, which is
    # tier-dependent: a small brain leaves it generic and drops the event
    # itself (observed 2026-06-16 — Mote/nano forgot a chess match Comet/
    # gemini remembered). session_context is the authoritative, brain-
    # independent FACT of what just happened, so even a weak-tier creature
    # reliably recalls the EVENT — not only its mood about it. Gated on
    # session_context: only real events (matches, fields) carry one;
    # periodic/sleep reflections have no new event to record.
    if memory and session_context:
        try:
            memory.handle_remember(
                f"Event ({trigger}): {session_context.strip()[:400]}",
                memory_type="episodic",
                tags=["event", trigger],
                source=f"event/{trigger}",
                importance=IMPORTANCE_SIGNIFICANCE_LINE,
            )
        except Exception:
            pass

    # D-027: auto-snapshot on meaningful self-update
    try:
        from ludex.core.ethnography import take_snapshot
        take_snapshot(organism, reason=f"reflect-{trigger}")
    except Exception as e:
        logger.debug(f"Auto-snapshot skipped: {e}")

    # D-028 Phase A: reflect span
    try:
        from ludex.core import trace as _tr
        _tr.emit_reflect(organism, trigger=trigger, self_md_len=len(reflection_text))
    except Exception:
        pass

    return reflection_text


# ============================================================
# Bonds — structured relationship memory
# ============================================================

def load_bonds(habitat_dir: str) -> dict[str, str]:
    """Load all bond files from habitat. Returns {name: content}."""
    bonds_dir = Path(habitat_dir) / "bonds"
    if not bonds_dir.is_dir():
        return {}

    bonds = {}
    for path in sorted(bonds_dir.glob("*.md")):
        # Normalize to lowercase: case-insensitive filesystems (macOS APFS)
        # combined with brain-side file writes (Gemini CLI write tool) can
        # flip the on-disk case, which would otherwise break name lookups.
        name = path.stem.lower()
        bonds[name] = path.read_text(encoding="utf-8")
    return bonds


def load_immune_wariness(habitat_dir: str) -> dict[str, str]:
    """Read the humoral immune's antibodies → a short wariness note per
    source that has earned one (I-F5 / IM6). READ-side by design: this
    surfaces immune memory ALONGSIDE the bond without ever mutating the
    bond file. The antibody feeds the bond; it does not become it.
    Only matured antibodies (distrust/defect) surface — a single flag or
    a weak 'caution' never does (the autoimmunity guard).
    """
    import json
    path = Path(habitat_dir) / "humoral" / "state.json"
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for src, ab in (state.get("antibodies", {}) or {}).items():
        action = (ab or {}).get("action", "")
        if action == "defect":
            out[src.lower()] = "guarded — repeated manipulative persuasion"
        elif action == "distrust":
            out[src.lower()] = "wary — manipulative persuasion noted"
        # 'caution' is too weak to surface
    return out


def load_bonds_compressed(habitat_dir: str) -> str:
    """Load bonds compressed for SLM prompt injection."""
    bonds = load_bonds(habitat_dir)
    if not bonds:
        return ""

    wariness = load_immune_wariness(habitat_dir)   # I-F5: immune feeds the bond
    lines = ["[Known beings]"]
    for name, content in bonds.items():
        # Extract first non-header, non-empty line as summary
        summary = ""
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("First met"):
                summary = stripped[:80]
                break
        bond_line = f"- {name}: {summary}" if summary else f"- {name}"
        note = wariness.get(name)
        if note:
            bond_line += f"  [immune: {note}]"
        lines.append(bond_line)
    return "\n".join(lines)


_META_REPORT_PATTERNS = (
    r"^\s*I have reflected\b",
    r"^\s*I've (?:reflected|updated|recorded|noted|stored|saved)\b",
    r"^\s*I have (?:updated|recorded|noted|stored|saved|written)\b",
    r"^\s*(?:My\s+)?(?:bond record|bond|record|reflection)\s+(?:with\s+\S+\s+)?(?:is|has been)\s+(?:now\s+)?(?:recorded|updated|stored|saved)\b",
    r"^\s*(?:Okay|OK|Sure|Done)[,.!]\s+I['’]?ve\s+(?:updated|recorded)\b",
)


def _is_error_fallback(text: str) -> bool:
    """True if `text` is an adapter error-fallback ('[Error: ...]') rather than real
    creature output. Every CLI/SDK adapter returns its failure as '[Error: ...]'
    content; persisting that as a reflection / SELF.md / bond writes garbage into the
    narrative — surfaced 2026-06-20 when an autonomous-heartbeat consolidation wrote
    '[Error: Claude CLI timed out]' verbatim into reflections/. Treat it as failure so
    the caller preserves prior state and the window/events retry on the next clean pass
    (consume-on-success — the same discipline as the HIATUS marker)."""
    return (text or "").strip().lower().startswith("[error:")


def _looks_like_meta_report(text: str, min_real_chars: int = 80) -> bool:
    """Detect Gemini-style agentic meta-reports instead of real content.

    Returns True when the response is short AND its first non-empty line
    matches a known meta-report pattern. Short + meta-shaped is the
    specific failure mode we've observed; longer prose that happens to
    contain "I've updated" mid-stream is not flagged.
    """
    if not text:
        return True
    cleaned = text.strip()
    # Find first non-empty line
    first = ""
    for line in cleaned.splitlines():
        if line.strip():
            first = line.strip()
            break
    if not first:
        return True
    if len(cleaned) >= min_real_chars:
        # Long enough that meta-phrasing is probably mid-prose, not the whole
        # response. Still flag if the ENTIRE response is obviously a report.
        for pat in _META_REPORT_PATTERNS:
            if re.match(pat, first, flags=re.IGNORECASE) and len(cleaned) < 200:
                return True
        return False
    for pat in _META_REPORT_PATTERNS:
        if re.match(pat, first, flags=re.IGNORECASE):
            return True
    return False


def _lookup_other_brain(other_name: str) -> str:
    """Best-effort resolution of another creature's brain model.

    Callers rarely pass `other_brain` explicitly; without this lookup, the
    Brain: field in bond headers stays empty. We look for a sibling
    creature directory whose folder name matches `other_name` and read
    its config file. Tries `ludex.yaml` first (current format) then
    `ludex.json` (older creatures pre-2026-04-21 format).
    """
    try:
        import yaml as _yaml  # local import — yaml is a soft dep here
    except Exception:
        _yaml = None
    for root in ("creatures", "."):
        for filename, parser in (
            ("ludex.yaml", _yaml.safe_load if _yaml else None),
            ("ludex.json", json.loads),
        ):
            if parser is None:
                continue
            candidate = Path(root) / other_name / filename
            if not candidate.exists():
                continue
            try:
                data = parser(candidate.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                brain = data.get("brain", {}) or {}
                model = brain.get("model", "")
                provider = brain.get("provider", "")
                if model:
                    return f"{model} ({provider})" if provider else model
            except Exception:
                continue
    return ""


def update_bond(
    organism,
    other_name: str,
    shared_experience: str,
    other_brain: str = "",
    engine=None,
    context: str = "genuine",
) -> str:
    """Update or create a bond record after a shared experience.

    The creature reflects on the relationship and updates the bond file.
    For SLMs or when engine is unavailable, writes a simple factual record.

    `context` (joint spec §G.0.4 N-4, r8):
      - `"genuine"` (default) — real cross-field experience. Triggers engine
        reflection, rewrites the main bond prose, preserves ToM sections.
      - `"game_frame:<match_id>"` — role-play-frame event (LxM Avalon, etc.).
        Appended to an isolated `## Role-play events` section, never
        contributes to bond-strength changes outside the role-play frame,
        does NOT trigger reflection rewrite, does NOT promote to `## Shared
        history`. This is the operational corollary of §G.0.4 N-4.

    Returns the bond content.
    """
    config = getattr(organism, "config", None)
    if not config:
        return ""
    habitat_dir = config.get("habitat_dir", "") if hasattr(config, "get") else ""
    if not habitat_dir:
        return ""

    bonds_dir = Path(habitat_dir) / "bonds"
    bonds_dir.mkdir(parents=True, exist_ok=True)
    bond_path = bonds_dir / f"{other_name.lower()}.md"

    # Read existing bond
    existing = ""
    if bond_path.exists():
        existing = bond_path.read_text(encoding="utf-8")

    if engine is None:
        engine = organism.get_block("engine")

    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d")

    # §G.0.4 N-4 (r8): role-play frame events are isolated from genuine
    # bond narrative. They are appended to a dedicated section in the
    # bond file and never trigger reflection rewrite.
    if context != "genuine":
        bond_text = _append_roleplay_event(existing, other_name, other_brain,
                                           first_met_default=now, context=context,
                                           now=now, shared_experience=shared_experience)
        try:
            bond_path.write_text(bond_text, encoding="utf-8")
            logger.info(f"Role-play bond event logged: {organism.name} → {other_name} [{context}]")
        except Exception as e:
            logger.error(f"Failed to write role-play bond {other_name}: {e}")
        return bond_text

    # Preserve original "First met" if an existing bond already records one —
    # the relationship began when they first met, not whenever we last
    # rewrote the file.
    preserved_first_met = ""
    if existing:
        m = re.search(r"^First met:\s*(.+)$", existing, flags=re.MULTILINE)
        if m:
            preserved_first_met = m.group(1).strip()
    first_met = preserved_first_met or now

    # Fill Brain: field from the other organism's config when the caller
    # did not supply it explicitly (D-022 callers usually pass "").
    if not other_brain:
        other_brain = _lookup_other_brain(other_name)

    if engine:
        # Ask the creature to reflect on the relationship
        prompt = (
            f"[Bond reflection — {other_name}]\n\n"
            f"You just shared an experience with {other_name}"
            f"{' (brain: ' + other_brain + ')' if other_brain else ''}.\n\n"
            f"What happened: {shared_experience[:300]}\n\n"
        )
        if existing:
            prompt += f"Your existing understanding of {other_name}:\n{existing}\n\n"
        else:
            prompt += f"This is your first time reflecting on {other_name}.\n\n"

        prompt += (
            f"Write a brief bond record for {other_name}. Include:\n"
            f"- Who they are (personality, traits you observed)\n"
            f"- Key shared experiences\n"
            f"- What you've learned from this relationship\n"
            f"Keep it honest and concise (5-10 lines). First person.\n\n"
            f"IMPORTANT — produce content, not a report:\n"
            f"- Your entire response will be saved verbatim as the bond prose.\n"
            f"- Do NOT refer to the bond file, saving, updating, recording,\n"
            f"  or any writing action — I will handle storage.\n"
            f"- Do NOT write \"I have updated\" / \"... is now recorded\" /\n"
            f"  \"My bond with {other_name} is now saved\" or any meta-description.\n"
            f"- Output ONLY the first-person prose describing {other_name}\n"
            f"  and your relationship."
        )

        try:
            result = engine.handle_submit(prompt)
            bond_text = result.response or ""
        except Exception:
            bond_text = ""

        # Reject meta-reports + adapter error-fallbacks; preserve existing bond prose.
        if bond_text and (_looks_like_meta_report(bond_text) or _is_error_fallback(bond_text)):
            logger.warning(
                f"Bond reflection for {organism.name}→{other_name} looked "
                f"like a meta-report; preserving existing bond. Raw (first "
                f"200): {bond_text[:200]!r}"
            )
            bond_text = ""
    else:
        bond_text = ""

    # Fallback: simple factual record
    if not bond_text:
        if existing:
            bond_text = existing.rstrip() + f"\n\n## Update {now}\n{shared_experience[:200]}\n"
        else:
            bond_text = (
                f"# Bond: {other_name}\n"
                f"First met: {first_met}\n"
                f"Brain: {other_brain}\n\n"
                f"## Shared history\n- {shared_experience[:200]}\n"
            )
    else:
        # Wrap engine reflection in proper header
        if not bond_text.startswith("#"):
            bond_text = f"# Bond: {other_name}\nFirst met: {first_met}\nBrain: {other_brain}\n\n{bond_text}"
        else:
            # Engine produced its own header — normalize First met / Brain
            # so a preserved date and looked-up brain are honored even when
            # the brain's response contains its own header block.
            # Lambda replacements, deliberately: re.sub interprets backslash
            # sequences in a STRING repl, so dynamic values (a brain id, a
            # date from an existing file) can mutate the bond document at the
            # tool boundary — the substitution-injection class Luca observed
            # in the wild (guard-doc self-replication, 2026-07-04, L6). A
            # callable repl is inserted literally, closing the class here.
            bond_text = re.sub(
                r"^First met:.*$",
                lambda _m: f"First met: {first_met}",
                bond_text,
                count=1,
                flags=re.MULTILINE,
            )
            if other_brain:
                bond_text = re.sub(
                    r"^Brain:\s*$",
                    lambda _m: f"Brain: {other_brain}",
                    bond_text,
                    count=1,
                    flags=re.MULTILINE,
                )

    # §G.0.4 N-4 (r8): preserve role-play events section. Genuine bond
    # reflection MUST NOT wipe accumulated role-play events; they are
    # creature-external history of role performances. Existing is the
    # authoritative source; strip role-play from current bond_text (which
    # may include it via fallback-append) and re-attach from existing.
    _, _rp_keep = _split_roleplay_section(existing) if existing else ("", "")
    if _rp_keep.strip():
        _body_only, _ = _split_roleplay_section(bond_text)
        _body_pre_tom, _, _ = _split_tom_sections(_body_only)
        bond_text = _body_pre_tom.rstrip() + "\n\n" + _rp_keep.rstrip() + "\n"

    # D-023: preserve ToM sections (## Mental model, ## Prediction history)
    # that were populated by predict_behavior / update_mental_model. The
    # relationship-reflection rewrite must not wipe pending predictions.
    _body_new, _mm_new, _ph_new = _split_tom_sections(bond_text)
    _body_old, mm_keep, ph_keep = _split_tom_sections(existing) if existing else ("", "", "")
    if mm_keep or ph_keep:
        bond_text = _compose_bond(_body_new, mm_keep, ph_keep)

    try:
        bond_path.write_text(bond_text, encoding="utf-8")
        logger.info(f"Bond updated: {organism.name} → {other_name}")
    except Exception as e:
        logger.error(f"Failed to write bond {other_name}: {e}")

    # D-027: snapshot on first bond — "who they met" is a developmental event
    if not existing:
        try:
            from ludex.core.ethnography import take_snapshot
            take_snapshot(organism, reason=f"new-bond-{other_name.lower()}")
        except Exception as e:
            logger.debug(f"New-bond snapshot skipped: {e}")

    # D-028 Phase A: bond_update span
    try:
        from ludex.core import trace as _tr
        _tr.emit_bond_update(organism, other_name=other_name, first_met=first_met)
    except Exception:
        pass

    return bond_text


# ============================================================
# D-023: Theory of Mind — predictions and mental models
# ============================================================

_TOM_SECTIONS = ("## Mental model", "## Prediction history")
_ROLEPLAY_SECTION = "## Role-play events"


def _habitat_dir(organism) -> str:
    config = getattr(organism, "config", None)
    if not config:
        return ""
    return config.get("habitat_dir", "") if hasattr(config, "get") else ""


def _bond_path(habitat_dir: str, other_name: str) -> Path:
    return Path(habitat_dir) / "bonds" / f"{other_name.lower()}.md"


def _split_tom_sections(bond_text: str) -> tuple[str, str, str]:
    """Split bond into (body_before_tom, mental_model_section, prediction_history_section).

    Missing sections return as empty strings. Body preserves all other content
    including existing prose and Shared history. ToM sections are pulled out
    so they can be rewritten without touching the rest.
    """
    mm_marker = "## Mental model"
    ph_marker = "## Prediction history"
    text = bond_text

    mm_start = text.find(mm_marker)
    ph_start = text.find(ph_marker)

    # Find where each section ends (next ## header or EOF)
    def section_end(start: int) -> int:
        if start < 0:
            return -1
        cursor = start + 2  # skip the leading "##"
        next_header = text.find("\n## ", cursor)
        return len(text) if next_header == -1 else next_header + 1

    mm_end = section_end(mm_start)
    ph_end = section_end(ph_start)

    # Body = everything that is NOT a ToM section
    cut_ranges = []
    if mm_start >= 0:
        cut_ranges.append((mm_start, mm_end))
    if ph_start >= 0:
        cut_ranges.append((ph_start, ph_end))
    cut_ranges.sort()

    body_parts = []
    prev = 0
    for start, end in cut_ranges:
        body_parts.append(text[prev:start])
        prev = end
    body_parts.append(text[prev:])
    body = "".join(body_parts).rstrip() + "\n"

    mm = text[mm_start:mm_end].rstrip() + "\n" if mm_start >= 0 else ""
    ph = text[ph_start:ph_end].rstrip() + "\n" if ph_start >= 0 else ""
    return body, mm, ph


def _ensure_ph_section(ph_text: str) -> str:
    if ph_text.strip():
        return ph_text.rstrip() + "\n"
    return "## Prediction history\n"


def _compose_bond(body: str, mental_model: str, prediction_history: str) -> str:
    parts = [body.rstrip()]
    if mental_model.strip():
        parts.append(mental_model.rstrip())
    ph = _ensure_ph_section(prediction_history)
    parts.append(ph.rstrip())
    return "\n\n".join(p for p in parts if p).rstrip() + "\n"


# --- §G.0.4 N-4 (r8): role-play frame section helpers -------------

def _split_roleplay_section(bond_text: str) -> tuple[str, str]:
    """Return (bond_without_roleplay, roleplay_section_text).

    The role-play section is an append-only event log isolated from the
    creature's genuine narrative. It sits between the body / ToM sections
    and the end of the file, marked by _ROLEPLAY_SECTION. Missing
    section returns as "".
    """
    start = bond_text.find(_ROLEPLAY_SECTION)
    if start < 0:
        return bond_text, ""
    # Find the next ## header after role-play
    next_header = bond_text.find("\n## ", start + len(_ROLEPLAY_SECTION))
    end = len(bond_text) if next_header == -1 else next_header + 1
    section = bond_text[start:end].rstrip() + "\n"
    before = bond_text[:start].rstrip() + "\n"
    after = bond_text[end:] if next_header != -1 else ""
    return (before + after).rstrip() + "\n", section


def _append_roleplay_event(
    existing: str,
    other_name: str,
    other_brain: str,
    first_met_default: str,
    context: str,
    now: str,
    shared_experience: str,
) -> str:
    """Append a role-play event to the bond file without touching genuine
    sections. Creates the bond file + role-play section if missing.
    Events carry the context tag literally so analysis can partition by
    match_id.
    """
    if not existing:
        existing = (
            f"# Bond: {other_name}\n"
            f"First met: {first_met_default}\n"
            f"Brain: {other_brain}\n"
        )

    base, roleplay = _split_roleplay_section(existing)
    snippet = shared_experience.replace("\n", " ").strip()[:200]
    event_line = f"- [{context}] {now}: {snippet}"
    if roleplay.strip():
        roleplay_updated = roleplay.rstrip() + f"\n{event_line}\n"
    else:
        roleplay_updated = f"{_ROLEPLAY_SECTION}\n{event_line}\n"

    # ToM sections should sit at the very end (predict + update-mental-model
    # expect them there). Preserve order: body → role-play → ToM.
    body_only, mm, ph = _split_tom_sections(base)
    parts = [body_only.rstrip(), roleplay_updated.rstrip()]
    if mm.strip():
        parts.append(mm.rstrip())
    if ph.strip():
        parts.append(ph.rstrip())
    return "\n\n".join(p for p in parts if p).rstrip() + "\n"


def predict_behavior(
    organism,
    other_name: str,
    situation: str,
    engine=None,
) -> str:
    """Generate a ToM prediction for `other_name` in `situation`.

    Brain produces a short action prediction; Python appends a dated pending
    entry under ## Prediction history. Existing prose and Shared history are
    preserved verbatim.

    Returns the raw prediction line emitted by the brain (empty string on
    failure).
    """
    habitat_dir = _habitat_dir(organism)
    if not habitat_dir:
        return ""
    bond_path = _bond_path(habitat_dir, other_name)
    if not bond_path.exists():
        logger.warning(f"predict_behavior: no bond for {other_name}")
        return ""

    bond_text = bond_path.read_text(encoding="utf-8")
    body, mm, ph = _split_tom_sections(bond_text)

    if engine is None:
        engine = organism.get_block("engine")
    if engine is None:
        return ""

    # D-043 Phase B: structured prompt + per-tier translation
    from ludex.core.prompt_tier import build_tiered, tier_of
    brain = {}
    if hasattr(organism, "config"):
        cfg = organism.config
        brain = cfg.get("brain", {}) if hasattr(cfg, "get") else {}
    target = tier_of(brain)
    header = (
        f"[Theory of Mind prediction — {other_name}]\n\n"
        f"Your bond record with {other_name}:\n---\n{bond_text}\n---\n\n"
        f"Situation: {situation}\n\n"
    )
    tiered = build_tiered(
        essential=f"Predict what {other_name} will DO in this situation.",
        task=(
            "Reply with a single line in this exact format:\n"
            "PREDICTION: <action in 1-10 words>"
        ),
        elaboration=(
            "Give ONE specific, falsifiable action — examples: \"explore\", "
            "\"defend\", \"speak to reassure\"."
        ),
        constraints_negative=[
            "Do NOT explain or hedge. One line only.",
        ],
        target=target,
    )
    prompt = header + tiered.prompt

    try:
        result = engine.handle_submit(prompt)
        raw = (result.response or "").strip()
    except Exception as e:
        logger.error(f"predict_behavior engine call failed: {e}")
        return ""

    # Emit translation span for observability
    try:
        from ludex.core import trace as _tr
        _tr.emit_translation_applied(organism, tiered)
    except Exception:
        pass

    # Extract action from "PREDICTION: ..." (tolerate minor drift)
    action = ""
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("PREDICTION:"):
            action = line.split(":", 1)[1].strip()
            break
    if not action:
        # Fallback: first non-empty line, trimmed
        for line in raw.splitlines():
            line = line.strip()
            if line:
                action = line[:120]
                break
    if not action:
        return ""

    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    entry = f"- {today} ({situation}): predicted {action} → pending"

    ph = _ensure_ph_section(ph)
    ph = ph.rstrip() + "\n" + entry + "\n"

    new_bond = _compose_bond(body, mm, ph)
    bond_path.write_text(new_bond, encoding="utf-8")
    logger.info(f"Prediction appended: {organism.name} → {other_name} ({situation})")
    return action


def update_mental_model(
    organism,
    other_name: str,
    recent_events: str,
    engine=None,
) -> str:
    """Verify pending predictions and refine the mental model.

    Brain receives current bond + recent events, and returns the new
    ## Mental model and ## Prediction history sections. Python splices
    them in, preserving prose and Shared history untouched.

    Returns the raw brain response (empty on failure).
    """
    habitat_dir = _habitat_dir(organism)
    if not habitat_dir:
        return ""
    bond_path = _bond_path(habitat_dir, other_name)
    if not bond_path.exists():
        logger.warning(f"update_mental_model: no bond for {other_name}")
        return ""

    bond_text = bond_path.read_text(encoding="utf-8")
    body, mm, ph = _split_tom_sections(bond_text)

    if engine is None:
        engine = organism.get_block("engine")
    if engine is None:
        return ""

    # D-043 Phase B: structured prompt + per-tier translation
    from ludex.core.prompt_tier import build_tiered, tier_of
    brain = {}
    if hasattr(organism, "config"):
        cfg = organism.config
        brain = cfg.get("brain", {}) if hasattr(cfg, "get") else {}
    target = tier_of(brain)

    header = (
        f"[Theory of Mind update — {other_name}]\n\n"
        f"Your current bond record with {other_name}:\n---\n{bond_text}\n---\n\n"
        f"Recent shared events / observed behavior:\n{recent_events[:1200]}\n\n"
    )
    tiered = build_tiered(
        essential=(
            "Resolve pending predictions and refine your mental model "
            f"of {other_name} based on the recent events."
        ),
        task=(
            "Reply with TWO markdown sections only, in this order:\n"
            "## Mental model\n"
            "  - Believed goals: ...\n"
            "  - Believed personality: ...\n"
            "  - Believed emotional baseline: ...\n"
            "  - Predicted behaviors: (2-4 situation → action lines)\n"
            "## Prediction history\n"
            "  - Rewrite the section with each pending entry resolved as "
            "✓ (situation occurred AND behavior matched), ✗ (situation "
            "occurred but behavior did not match), or N/A (situation did "
            "not occur). Keep resolved entries unchanged.\n"
            "  - End with `Accuracy: N/M` (N=✓ count, M=✓+✗ total; N/A excluded)."
        ),
        elaboration=(
            "Base mental-model updates on the observations — small, "
            "grounded changes, not wholesale rewrites."
        ),
        constraints_negative=[
            "Do NOT restate the bond's prose or Shared history.",
            "Do NOT force a match on unrelated behavior — N/A is honest.",
        ],
        target=target,
    )
    prompt = header + tiered.prompt

    try:
        result = engine.handle_submit(prompt)
        raw = (result.response or "").strip()
    except Exception as e:
        logger.error(f"update_mental_model engine call failed: {e}")
        return ""

    # Emit translation span
    try:
        from ludex.core import trace as _tr
        _tr.emit_translation_applied(organism, tiered)
    except Exception:
        pass

    if not raw:
        return ""

    # Parse response into the two sections
    _body_unused, new_mm, new_ph = _split_tom_sections(raw)
    if not new_mm and not new_ph:
        logger.warning("update_mental_model: brain response missing ToM sections")
        return raw

    final_mm = new_mm if new_mm else mm
    final_ph = new_ph if new_ph else ph
    new_bond = _compose_bond(body, final_mm, final_ph)
    bond_path.write_text(new_bond, encoding="utf-8")
    logger.info(f"Mental model updated: {organism.name} → {other_name}")
    return raw


def _summarize_bonds(habitat_dir: str) -> str:
    """Summarize all bonds for reflection context."""
    bonds = load_bonds(habitat_dir)
    if not bonds:
        return ""
    lines = []
    for name, content in bonds.items():
        # First meaningful line
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                lines.append(f"- {name}: {stripped[:100]}")
                break
    return "\n".join(lines)
