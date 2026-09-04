"""Caretaker-safe persistence boundaries for bounded production turns.

This module is deliberately village-neutral.  A caretaker runner supplies its
own repository root, creature, provenance label, and scheduling policy; the
shared layer only protects authorized context and the resulting artifact.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


class ProductionTurnError(ValueError):
    """A bounded turn cannot be persisted without crossing its contract."""


@dataclass(frozen=True)
class PersistedSession:
    """Paths and text that survived a bounded creature session."""

    artifact: Path
    reply: Path | None
    source: str
    memory_excerpt: str


def file_digest(path: Path) -> str | None:
    """Return a content digest, or ``None`` when the path does not exist."""

    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(path: Path, repository_root: Path) -> str:
    root = repository_root.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ProductionTurnError(f"path is outside the repository: {resolved}")
    return resolved.relative_to(root).as_posix()


def _unique_reply_path(out_path: Path) -> Path:
    first = Path(f"{out_path}.reply.md")
    if not first.exists():
        return first
    index = 2
    while True:
        candidate = Path(f"{out_path}.reply-{index}.md")
        if not candidate.exists():
            return candidate
        index += 1


def _memory_excerpt(path: Path, limit: int = 1200) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[:limit].strip()


def memory_record(
    *,
    why: str,
    work_id: str,
    artifact: str,
    reply_artifact: str | None,
    source: str,
    excerpt: str,
) -> str:
    """Build a provenance-bearing episodic summary for the caretaker."""

    reply_clause = (
        f" Reply artifact: {reply_artifact}." if reply_artifact is not None else ""
    )
    return (
        f"{why} Work: {work_id}. Artifact: {artifact}.{reply_clause} "
        f"Artifact source: {source}. What I made: {excerpt}"
    )


def assemble_prompt(
    repository_root: Path,
    prompt_path: Path,
    context_paths: list[Path],
    *,
    content_sealed: bool = False,
) -> tuple[str, list[dict[str, str]]]:
    """Assemble a prompt and record the exact authorized context bytes.

    A content-sealed turn carries all source text in-band and tells the brain
    not to retrieve anything else.  The caller must separately submit with
    tools, memory recall, and prior history disabled.
    """

    _relative_path(prompt_path, repository_root)
    prompt = prompt_path.read_text(encoding="utf-8")
    evidence: list[dict[str, str]] = []
    for context_path in context_paths:
        locator = _relative_path(context_path, repository_root)
        payload = context_path.read_bytes()
        try:
            context = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProductionTurnError(
                f"authorized context is not UTF-8 text: {context_path}"
            ) from exc
        evidence.append({
            "path": locator,
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
        prompt += (
            f"\n\n---\nAuthorized production context: {locator}\n\n{context}"
        )
    if content_sealed:
        if not context_paths:
            raise ProductionTurnError(
                "--content-sealed requires at least one --context-file"
            )
        prompt = (
            "CONTENT-SEALED PRODUCTION TURN. Use only the source text embedded "
            "below plus your system identity. Do not retrieve or list files, "
            "use tools, web search, prior conversational history, or recalled "
            "private memory. If the embedded evidence is insufficient, return "
            "an exact blocker instead of looking elsewhere.\n\n"
            + prompt
        )
    return prompt, evidence


def persist_session_result(
    out_path: Path,
    response: str,
    before_digest: str | None,
    *,
    allow_overwrite: bool = False,
) -> PersistedSession:
    """Persist a reply without destroying an artifact written by the creature.

    Agentic adapters may write ``out_path`` during a turn and then return only
    a short note (or no text).  A content digest, rather than a timestamp,
    decides whether the creature already produced the artifact.  Any returned
    note goes to a unique sidecar so both pieces of evidence survive.
    """

    response = response.strip()
    after_digest = file_digest(out_path)
    creature_wrote_artifact = after_digest is not None and after_digest != before_digest

    if creature_wrote_artifact:
        reply_path = None
        if response:
            reply_path = _unique_reply_path(out_path)
            reply_path.write_text(response + "\n", encoding="utf-8", newline="\n")
        excerpt = _memory_excerpt(out_path)
        if response:
            excerpt = f"Artifact excerpt: {excerpt}\nReply: {response[:600]}"
        return PersistedSession(
            artifact=out_path,
            reply=reply_path,
            source="creature_write",
            memory_excerpt=excerpt,
        )

    if not response:
        raise ProductionTurnError(
            "empty response and no new artifact; nothing was written"
        )
    if after_digest is not None and not allow_overwrite:
        raise ProductionTurnError(
            f"artifact already exists and was unchanged: {out_path}; "
            "choose a new path or pass --overwrite"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(response + "\n", encoding="utf-8", newline="\n")
    return PersistedSession(
        artifact=out_path,
        reply=None,
        source="runner_reply",
        memory_excerpt=response[:1200],
    )
