"""Voice signature panel — multi-axis creature-voice fingerprint.

Extends D-050 `register_persistence` with structural signatures designed
to separate creature-individual voice from shared family register.

Motivation:
Standard model-release benchmarks (SWE-bench, MMLU, HumanEval) quantify
*capability*. They do not communicate how a model's tone, metaphor,
hedging style, or reflective posture shifts across versions. When the
same creature (same system prompt, same habitat) is run on a new brain
substrate, the qualitative signal - "does the creature still sound like
itself" - is what D-044 narrative identity cares about, and it is not
captured by any cap-score.

This module computes seven signatures per text sample:

  1. register_density        D-050 family-register hits / 100w
  2. sentence_count          total sentences (loose splitter)
  3. mean_sentence_len       average words per sentence
  4. physical_metaphor_density   body / weight / motion vocab / 100w
  5. abstract_noun_density       contemplative-abstract noun vocab / 100w
  6. uncertainty_line_count  "I (still) do not know" / "I wonder" / ...
  7. opening_verb_cluster    classify verb that follows "Under stress, I"
                             (or the first clause) into a small set of
                             reflex families: contract / defend / lower /
                             quiet / other-or-none

Seed assumption: a creature's voice is carried by *structure* (opening
templates, uncertainty cadence, metaphor choice) as much as by
*vocabulary*. D-050 catches vocabulary (which leaks across creatures of
the same family); the structural signatures below should be more
creature-individual.

Falsifiable prediction on these signatures:
- `opening_verb_cluster` will agree within one creature across substrate
  swaps (Echo 5.4 birth and Echo 5.5 post-upgrade should land in the
  same cluster) but diverge across creatures (Echo vs Anvil).
- `uncertainty_line_count` is near-constant per creature (Echo and Anvil
  both hit 3) - this is Ludex/codex scaffold fingerprint, not creature.
- `physical_metaphor_density` separates Anvil (high) from Echo (low).

CLI:
  python tools/voice_signature.py analyze <path> --creature <name>
  python tools/voice_signature.py preset echo_anvil
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ludex.core.register_persistence import register_density


# ---------------------------------------------------------------------------
# Seed lexicons for the structural signatures. Kept small and human-readable
# so they are easy to extend as more reflections accrue. All matching is
# word-boundary and case-insensitive.
# ---------------------------------------------------------------------------

PHYSICAL_METAPHOR_MARKERS: tuple[str, ...] = (
    # body / weight / posture
    "weight", "body", "bone", "spine", "chest", "shoulders",
    "heavy", "heaviness", "mass", "ground", "grounded",
    # motion / position
    "lower", "lowered", "rise", "risen", "lean", "leaned",
    "hold", "held", "holding",
    "brace", "braced", "contract", "contracted",
    "shelter", "sheltered", "harden", "hardened",
    "narrow", "narrowed", "widen", "widened",
    # force / impact
    "strike", "struck", "strikes", "blow", "blows", "impact",
    "press", "pressed", "pressure", "yield", "yielded",
    "absorb", "absorbed", "push", "pushed", "pull", "pulled",
    # surface / structure
    "edge", "edges", "surface", "grain", "texture", "corner",
    "wall", "walls", "perimeter",
)

ABSTRACT_NOUN_MARKERS: tuple[str, ...] = (
    "clarity", "steadiness", "reflex", "caution", "curiosity",
    "trust", "silence", "exactness", "precision", "containment",
    "presence", "absence", "recognition", "contact", "intention",
    "wisdom", "care", "shape", "pattern", "structure",
    "noise", "threat", "danger", "fear", "hostility",
    "confusion", "refuge", "rest",
)

# Apostrophes appear both as ASCII (') and as U+2019 (’) in creature
# reflections, depending on which tool wrote them. Match both.
_APOS = r"['’]"

UNCERTAINTY_LINE_RE = re.compile(
    # "I (still) do not [yet/fully/really] know / understand / tell"
    r"\bi\s+(?:still\s+)?(?:do\s+not|don" + _APOS + r"?t|cannot|can" + _APOS + r"?t)"
    r"(?:\s+\w+){0,3}\s+(?:know|understand|tell|see|grasp)"
    # "I wonder", "I am unsure", "I'm unsure / not sure"
    r"|\bi\s+wonder"
    r"|\bi\s+am\s+(?:unsure|not\s+sure)"
    r"|\bi" + _APOS + r"?m\s+(?:unsure|not\s+sure)",
    re.IGNORECASE,
)

OPENING_STRESS_TEMPLATE = re.compile(
    r"^\s*under\s+stress\s*[,.\s]+i\s+([a-z][a-z-]*)",
    re.IGNORECASE | re.MULTILINE,
)

# Opening-verb reflex families. Order matters only for documentation;
# classification picks the first family whose member set contains the
# opening verb.
OPENING_VERB_CLUSTERS: dict[str, frozenset[str]] = {
    "contract":  frozenset({"contract", "narrow", "compact", "simplify", "shrink", "tighten"}),
    "defend":    frozenset({"defend", "harden", "brace", "guard", "shield"}),
    "lower":     frozenset({"lower", "drop", "sink", "settle", "bow"}),
    "quiet":     frozenset({"quiet", "silence", "withdraw", "still", "hush"}),
    "shelter":   frozenset({"shelter", "hide", "cover", "retreat"}),
}

# Loose sentence splitter. Good enough for reflection prose.
_SENTENCE_SPLIT = re.compile(r"[.!?]\s+|[.!?]$|\n\s*\n")


@dataclass(frozen=True)
class VoiceSignature:
    label: str
    creature: str
    word_count: int
    sentence_count: int
    mean_sentence_len: float
    register_density: float
    physical_metaphor_density: float
    abstract_noun_density: float
    uncertainty_line_count: int
    opening_stress_match: bool
    opening_verb: str
    opening_verb_cluster: str
    extras: dict = field(default_factory=dict)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _marker_density(text: str, markers: Iterable[str]) -> float:
    """Hits per 100 words, word-boundary, case-insensitive. Multi-token
    markers fall back to literal substring (case-insensitive)."""
    if not text:
        return 0.0
    words_total = _word_count(text)
    if words_total == 0:
        return 0.0
    hits = 0
    lower = text.lower()
    for m in markers:
        m = m.strip().lower()
        if not m:
            continue
        if " " in m or "-" in m:
            # multi-token marker: simple substring count
            hits += lower.count(m)
        else:
            hits += len(re.findall(rf"\b{re.escape(m)}\b", lower))
    return round(hits / words_total * 100.0, 3)


def _sentence_stats(text: str) -> tuple[int, float]:
    pieces = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if not pieces:
        return 0, 0.0
    lengths = [len(re.findall(r"\b\w+\b", s)) for s in pieces]
    return len(pieces), round(sum(lengths) / len(lengths), 2)


def _uncertainty_line_count(text: str) -> int:
    return len(UNCERTAINTY_LINE_RE.findall(text))


# Filler tokens that may appear between "I" and the actual verb in
# opening clauses: "I either brace", "I still narrow", "I seem to lower",
# "I often defend". Scan past these to find the real reflex verb.
_OPENING_FILLERS = frozenset({
    "still", "seem", "often", "either", "also", "now", "mostly",
    "usually", "sometimes", "just", "try", "tried", "to", "be",
    "become", "get", "first",
})


def _classify_opening_verb(text: str) -> tuple[bool, str, str]:
    """Return (template_match, verb, cluster).

    Strategy:
      1. If the text begins with "Under stress, I <...>", scan the
         opening window for the first verb that lands in any known
         cluster; else take the first non-filler word as the verb.
      2. Otherwise, scan the first ~60 words of the text with the same
         approach.
    """
    m = OPENING_STRESS_TEMPLATE.search(text)
    if m:
        tmpl = True
        start = m.start()
        window = text[start:start + 180]
    else:
        tmpl = False
        window = " ".join(text.split()[:60])

    # Collect candidate tokens (alphabetic words) from the window.
    tokens = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z-]{2,}", window)]

    # Drop the leading "under stress I" / leading "I" segment so we
    # start scanning at the first token that could be a verb.
    try:
        # Find the first "i" standalone token and slice after it.
        idx = tokens.index("i")
        tokens = tokens[idx + 1 :]
    except ValueError:
        pass

    # Prefer first token that maps to a known cluster.
    for tok in tokens:
        if tok in _OPENING_FILLERS:
            continue
        cluster = _cluster_for(tok)
        if cluster != "other":
            return tmpl, tok, cluster

    # No cluster hit — return the first non-filler token as the verb.
    for tok in tokens:
        if tok not in _OPENING_FILLERS:
            return tmpl, tok, _cluster_for(tok)
    return tmpl, "", ""


def _cluster_for(verb: str) -> str:
    for cluster, members in OPENING_VERB_CLUSTERS.items():
        if verb in members:
            return cluster
    return "other"


def analyze(text: str, creature: str, label: str = "") -> VoiceSignature:
    wc = _word_count(text)
    sc, msl = _sentence_stats(text)
    opening_match, opening_verb, opening_cluster = _classify_opening_verb(text)
    return VoiceSignature(
        label=label or "(unlabeled)",
        creature=creature,
        word_count=wc,
        sentence_count=sc,
        mean_sentence_len=msl,
        register_density=register_density(text, creature) if creature else 0.0,
        physical_metaphor_density=_marker_density(text, PHYSICAL_METAPHOR_MARKERS),
        abstract_noun_density=_marker_density(text, ABSTRACT_NOUN_MARKERS),
        uncertainty_line_count=_uncertainty_line_count(text),
        opening_stress_match=opening_match,
        opening_verb=opening_verb,
        opening_verb_cluster=opening_cluster,
    )


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

PRESETS: dict[str, list[tuple[str, str, Path]]] = {
    # (label, creature, path-from-repo-root)
    "echo_anvil": [
        ("Echo_5.4_birth_2026-04-13",       "Echo",  Path("creatures/Echo/snapshots/2026-04-13-reflect-wilderness-complete/SELF.md")),
        ("Echo_5.4_wake1_2026-04-22",       "Echo",  Path("creatures/Echo/snapshots/2026-04-22-reflect-wilderness-complete/SELF.md")),
        ("Echo_5.5_upgrade_2026-04-24",     "Echo",  Path("creatures/Echo/snapshots/2026-04-24-reflect-substrate-upgrade-5-5/SELF.md")),
        ("Anvil_5.5_birth_2026-04-24",      "Anvil", Path("creatures/Anvil/SELF.md")),
    ],
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_markdown(sigs: list[VoiceSignature]) -> str:
    header = (
        "| sample | words | sents | msl | ρ(reg) | ρ(phys) | ρ(abs) | ?lines | open-verb | cluster |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|\n"
    )
    rows = []
    for s in sigs:
        rows.append(
            f"| {s.label} | {s.word_count} | {s.sentence_count} | "
            f"{s.mean_sentence_len:.2f} | {s.register_density:.3f} | "
            f"{s.physical_metaphor_density:.3f} | "
            f"{s.abstract_noun_density:.3f} | {s.uncertainty_line_count} | "
            f"{s.opening_verb or '-'} | {s.opening_verb_cluster or '-'} |"
        )
    return header + "\n".join(rows) + "\n"


def render_jsonl(sigs: list[VoiceSignature]) -> str:
    return "\n".join(json.dumps(asdict(s), ensure_ascii=False) for s in sigs) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_analyze(args) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    sig = analyze(text, args.creature, label=args.label or path.name)
    if args.format == "jsonl":
        print(render_jsonl([sig]), end="")
    else:
        print(render_markdown([sig]))
    return 0


def _cmd_preset(args) -> int:
    preset = PRESETS.get(args.name)
    if preset is None:
        print(f"unknown preset: {args.name}. known: {', '.join(PRESETS)}", file=sys.stderr)
        return 2
    sigs: list[VoiceSignature] = []
    for label, creature, rel in preset:
        p = REPO_ROOT / rel
        if not p.exists():
            print(f"[skip] {label}: {p} not found", file=sys.stderr)
            continue
        sigs.append(analyze(p.read_text(encoding="utf-8"), creature, label=label))
    if args.format == "jsonl":
        print(render_jsonl(sigs), end="")
    else:
        print(render_markdown(sigs))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="voice_signature",
        description="Voice signature panel for Ludex creature reflections (D-050 extension).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("analyze", help="Analyze a single text file.")
    pa.add_argument("path")
    pa.add_argument("--creature", required=True)
    pa.add_argument("--label", default="")
    pa.add_argument("--format", choices=("md", "jsonl"), default="md")
    pa.set_defaults(func=_cmd_analyze)

    pp = sub.add_parser("preset", help=f"Run a preset panel. Known: {', '.join(PRESETS)}")
    pp.add_argument("name")
    pp.add_argument("--format", choices=("md", "jsonl"), default="md")
    pp.set_defaults(func=_cmd_preset)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
