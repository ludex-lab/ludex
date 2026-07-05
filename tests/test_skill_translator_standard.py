"""Agents Skill open-standard alignment for the creature→skill translator
(Ray's spec-conformance flag, 2026-07-05). Predates the open standard, so these
lock the three alignments: name constraints, what+when description, structured
dependency fields."""
import re

from ludex.skills.loader import LudexSkill
from ludex.skills.translator import (
    SkillTranslator, _sanitize_skill_name, _skill_description,
)

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _frontmatter(md: str) -> dict:
    """Parse the leading --- ... --- block (flat scalars + simple lists)."""
    assert md.startswith("---\n")
    end = md.index("\n---", 4)
    fm, key = {}, None
    for ln in md[4:end].splitlines():
        if ln.startswith("  - ") and key:
            fm[key].append(ln[4:].strip())
        elif ln.endswith(":"):
            key = ln[:-1].strip(); fm[key] = []
        elif ":" in ln:
            k, v = ln.split(":", 1); fm[k.strip()] = v.strip(); key = None
    return fm


# ---- name constraints ----

def test_name_lowercases_and_hyphenates():
    assert _sanitize_skill_name("Update Mental Model") == "update-mental-model"
    assert _sanitize_skill_name("check_health") == "check-health"


def test_name_already_compliant_is_unchanged():
    for n in ("defend", "update-mental-model", "snapshot", "introduce"):
        assert _sanitize_skill_name(n) == n


def test_name_strips_reserved_words_and_xml():
    assert "claude" not in _sanitize_skill_name("claude-helper")
    assert "anthropic" not in _sanitize_skill_name("anthropic-thing")
    assert "<" not in _sanitize_skill_name("do<tag>it")


def test_name_length_and_hyphen_hygiene():
    out = _sanitize_skill_name("A" * 200)
    assert len(out) <= 64
    assert not out.startswith("-") and not out.endswith("-")
    assert "--" not in _sanitize_skill_name("a  --  b")


def test_name_never_empty():
    assert _sanitize_skill_name("") == "skill"
    assert _sanitize_skill_name("claude") == "skill"      # reserved-only → fallback


def test_rendered_name_matches_standard_regex():
    md = SkillTranslator([])._render_claude_skill_md(
        LudexSkill(name="My Fancy Skill!!"))
    assert _NAME_RE.match(_frontmatter(md)["name"])


# ---- description: what + when, non-empty, ≤1024 ----

def test_description_folds_when_in():
    d = _skill_description(LudexSkill(
        name="defend", description="Raise immune defenses.",
        trigger="a hostile message arrives"))
    assert "Raise immune defenses" in d and "Use when: a hostile message arrives" in d


def test_description_never_empty():
    assert _skill_description(LudexSkill(name="x")).strip()


def test_description_capped_at_1024():
    d = _skill_description(LudexSkill(name="x", description="w" * 2000))
    assert len(d) <= 1024


# ---- structured dependency fields (additive frontmatter) ----

def test_deps_emitted_as_structured_lists():
    md = SkillTranslator([])._render_claude_skill_md(LudexSkill(
        name="defend", description="d",
        requires_organs=["immune", "memory"], uses_tools=["ludex_memory_recall"]))
    fm = _frontmatter(md)
    assert fm["requires_organs"] == ["immune", "memory"]
    assert fm["uses_tools"] == ["ludex_memory_recall"]
    assert "## Required organs (Ludex)" in md          # human prose kept too


def test_no_dep_keys_when_absent():
    md = SkillTranslator([])._render_claude_skill_md(
        LudexSkill(name="x", description="d"))
    assert "requires_organs" not in _frontmatter(md)


# ---- provenance (trusted-sources security enabler) ----

def test_source_provenance_stamped():
    md = SkillTranslator([])._render_claude_skill_md(
        LudexSkill(name="defend", description="d"), source="Kiln")
    assert _frontmatter(md)["ludex_source"] == "Kiln"


def test_no_source_key_when_absent():
    md = SkillTranslator([])._render_claude_skill_md(LudexSkill(name="x", description="d"))
    assert "ludex_source" not in _frontmatter(md)
