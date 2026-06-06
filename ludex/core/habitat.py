"""
Habitat — 에이전트의 활동 공간 (서식지)

Membrane = "누구와 통신할 수 있나" (사회적 경계)
Habitat  = "어디서 살 수 있나" (물리적 경계)

Habitat Modes:
- temporary: 세션 전용, 메모리에만 존재 (데모/테스트)
- local: 지정 폴더에 저장, 세션 간 유지
- portable: USB/외장 드라이브, 다른 컴퓨터에서도 동작

저장 구조:
  my-agent/
    ludex.yaml         # 장기 설정 + 뇌 설정 + habitat 설정
    memory/            # MemoryBlock 데이터
    immune/            # HumoralImmune 항원 기억
    logs/              # TrackingBlock 로그
"""

from __future__ import annotations

import os
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# D-060 host-level machine identity
# ----------------------------------------------------------------------
# `machine_id` is supposed to be machine-scope — every creature on the
# same physical machine shares one id, so a remote viewer can see
# "these two creatures are from the same host." The source of truth is
# a single file under the user's home directory; each habitat just
# mirrors the value. Discovered 2026-04-23 by Mac Ludex-Cody during
# D-058–061 rollout when 8 Mac creatures got 8 different UUIDs — the
# initial implementation generated the id inside `ensure_machine_id()`
# without consulting the host-level file, so it was effectively
# per-habitat.

_HOST_MACHINE_ID_ENV = "LUDEX_MACHINE_ID_PATH"
_DEFAULT_HOST_MACHINE_ID_RELATIVE = Path(".ludex") / "machine_id"

# Host-level habitat-origin marker — opt-in canonical-host guard.
# When set, `OrganismConfig.build()` refuses to activate a persistent
# creature whose `habitat.origin` doesn't match. Stops cross-machine
# pollution like the smoke_016 incident (Verse-on-Mac stub got
# touched on Windows, nearly polluting Mac canonical state). When
# absent, the guard is silently disabled — existing flows unaffected.
_HOST_HABITAT_ORIGIN_ENV_VALUE = "LUDEX_HABITAT_ORIGIN"          # direct value override
_HOST_HABITAT_ORIGIN_ENV_PATH = "LUDEX_HABITAT_ORIGIN_PATH"      # for tests / non-standard layouts
_DEFAULT_HOST_HABITAT_ORIGIN_RELATIVE = Path(".ludex") / "habitat_origin"


class HabitatMismatchError(RuntimeError):
    """Raised when a persistent creature is built on a host whose
    habitat-origin doesn't match the creature's `habitat.origin`."""


def _host_machine_id_path() -> Path:
    """Return the path to the host-level machine_id file.

    Resolution order:
    1. `$LUDEX_MACHINE_ID_PATH` (for tests and non-standard layouts).
    2. `~/.ludex/machine_id` on every platform (Windows included —
       `pathlib.Path.home()` returns the user profile dir and hidden-
       file conventions work fine under a dot-prefixed subdirectory).
    """
    override = os.environ.get(_HOST_MACHINE_ID_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / _DEFAULT_HOST_MACHINE_ID_RELATIVE


def _read_host_machine_id() -> str:
    """Read the host-level machine_id. Returns empty string if the file
    is absent or unreadable (caller decides whether to create one)."""
    try:
        path = _host_machine_id_path()
        if not path.exists():
            return ""
        value = path.read_text(encoding="utf-8").strip()
        return value
    except Exception:
        logger.debug("failed to read host machine_id", exc_info=True)
        return ""


def _write_host_machine_id(value: str) -> None:
    """Create `~/.ludex/` if needed and write the id. Swallow I/O
    errors so creature boot doesn't fail when the user home is
    read-only — the habitat's own `machine_id` still gets a valid
    value in-memory; the convergence property is lost until the
    filesystem is writable again."""
    try:
        path = _host_machine_id_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except Exception:
        logger.warning(
            "could not persist host machine_id to %s — "
            "creatures on this host may get different ids until the "
            "filesystem becomes writable.",
            _host_machine_id_path(),
            exc_info=True,
        )


def get_host_machine_id() -> str:
    """Public getter: return the host-level machine_id, creating it
    if absent. Idempotent — subsequent calls return the same value.
    This is the single source of truth that `HabitatConfig.ensure_machine_id`
    consults on every save."""
    existing = _read_host_machine_id()
    if existing:
        return existing
    new_id = str(uuid.uuid4())
    _write_host_machine_id(new_id)
    return new_id


def _host_habitat_origin_path() -> Path:
    """Resolve the host-level habitat_origin file. Resolution order:
    1. `$LUDEX_HABITAT_ORIGIN_PATH` (tests / non-standard layouts).
    2. `~/.ludex/habitat_origin`.
    """
    override = os.environ.get(_HOST_HABITAT_ORIGIN_ENV_PATH, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / _DEFAULT_HOST_HABITAT_ORIGIN_RELATIVE


def get_host_habitat_origin() -> str:
    """Return the host-level habitat-origin tag, or empty string when
    unset (in which case the canonical-host guard is disabled).

    Resolution order:
    1. `$LUDEX_HABITAT_ORIGIN` (direct value).
    2. File at `_host_habitat_origin_path()` (default `~/.ludex/habitat_origin`).
    3. Empty string.
    """
    direct = os.environ.get(_HOST_HABITAT_ORIGIN_ENV_VALUE, "").strip()
    if direct:
        return direct
    try:
        path = _host_habitat_origin_path()
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        logger.debug("failed to read host habitat_origin", exc_info=True)
        return ""


@dataclass
class HabitatConfig:
    """에이전트의 활동 공간 설정."""
    mode: str = "temporary"          # "temporary", "local", "portable"
    home_dir: str = ""               # 로컬/포터블 경로
    max_storage_mb: int = 500        # 저장 한도
    allow_network: bool = False      # 네트워크 접근 허용
    persistent: bool = False         # 세션 간 유지
    origin: str = ""                 # habitat identity tag per D-052
                                     # (e.g. "Ray-habitat", "Mac-habitat");
                                     # heartbeat scopes pulses by this.
    machine_id: str = ""             # globally unique machine UUID (D-060).
                                     # Mirrored from host-level source
                                     # of truth (`~/.ludex/machine_id`)
                                     # on every save, so siblings on
                                     # the same host share one id.
                                     # Stale values get overwritten by
                                     # the host-level id automatically.
    machine_alias: str = ""          # human-readable name ("Home Lab").
                                     # User-set; may collide across users —
                                     # machine_id handles uniqueness.
                                     # Empty → fallback to OS hostname.

    @classmethod
    def temporary(cls) -> HabitatConfig:
        """임시 서식지 — 세션 종료 시 사라짐."""
        return cls(mode="temporary", persistent=False)

    @classmethod
    def local(cls, path: str, max_mb: int = 500) -> HabitatConfig:
        """로컬 서식지 — 지정 폴더에 저장."""
        return cls(mode="local", home_dir=path, max_storage_mb=max_mb, persistent=True)

    @classmethod
    def portable(cls, path: str, max_mb: int = 500) -> HabitatConfig:
        """포터블 서식지 — USB 등 이동식 저장소."""
        return cls(mode="portable", home_dir=path, max_storage_mb=max_mb, persistent=True)

    def ensure_dirs(self):
        """서식지 디렉토리 생성 + 기본 스킬 설치."""
        if not self.home_dir or self.mode == "temporary":
            return
        base = Path(self.home_dir)
        base.mkdir(parents=True, exist_ok=True)
        (base / "memory").mkdir(exist_ok=True)
        (base / "immune").mkdir(exist_ok=True)
        (base / "logs").mkdir(exist_ok=True)
        (base / "bonds").mkdir(exist_ok=True)
        # Create empty SELF.md if it doesn't exist (D-021: emerges from reflection)
        self_path = base / "SELF.md"
        if not self_path.exists():
            self_path.write_text(
                f"# {base.name} — Self-Understanding\n\n"
                f"*This file is empty at birth. It grows through reflection.*\n",
                encoding="utf-8",
            )
        # Default skills are written by write_identity_files() which knows the organs

    def get_path(self, subdir: str = "") -> str:
        """서식지 내 경로 반환."""
        if not self.home_dir:
            return ""
        if subdir:
            return str(Path(self.home_dir) / subdir)
        return self.home_dir

    def measure_weight(self) -> dict:
        """
        Measure the creature's current "body weight" — total storage used.

        Returns dict with per-subdirectory breakdown and total.
        Biological metaphor: body weight/volume. Each organ contributes.
        When total exceeds max_storage_mb, the creature needs a "diet"
        (consolidation/forgetting) or a "weight limit increase" (habitat upgrade).
        """
        if not self.home_dir or not Path(self.home_dir).exists():
            return {"total_bytes": 0, "total_mb": 0.0, "max_mb": self.max_storage_mb, "usage_pct": 0.0, "breakdown": {}}

        import os
        breakdown = {}
        total = 0
        base = Path(self.home_dir)
        for item in base.iterdir():
            if item.is_file():
                size = item.stat().st_size
                breakdown[item.name] = size
                total += size
            elif item.is_dir():
                dir_size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                breakdown[item.name + "/"] = dir_size
                total += dir_size

        total_mb = total / (1024 * 1024)
        usage_pct = (total_mb / self.max_storage_mb * 100) if self.max_storage_mb > 0 else 0.0

        return {
            "total_bytes": total,
            "total_mb": round(total_mb, 2),
            "max_mb": self.max_storage_mb,
            "usage_pct": round(usage_pct, 1),
            "overweight": total_mb > self.max_storage_mb,
            "breakdown": {k: round(v / 1024, 1) for k, v in sorted(breakdown.items(), key=lambda x: -x[1])},  # KB
        }

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "home_dir": self.home_dir,
            "max_storage_mb": self.max_storage_mb,
            "allow_network": self.allow_network,
            "persistent": self.persistent,
            "origin": self.origin,
            "machine_id": self.machine_id,
            "machine_alias": self.machine_alias,
        }

    def ensure_machine_id(self) -> str:
        """Mirror the host-level machine_id onto this habitat (D-060).

        Called automatically on save. The host-level file
        (`~/.ludex/machine_id`, overrideable via
        `$LUDEX_MACHINE_ID_PATH`) is the source of truth. If the
        habitat's stored `machine_id` disagrees with it — including
        the common case where an older copy of Ludex wrote a
        per-habitat UUID — the host value wins and a migration log
        line is emitted so the change is discoverable from logs.

        Returns the resulting (host-level) id.
        """
        host_id = get_host_machine_id()
        if host_id and self.machine_id and self.machine_id != host_id:
            logger.info(
                "HabitatConfig: overwriting stale per-habitat "
                "machine_id %s with host-level %s (D-060 fix)",
                self.machine_id, host_id,
            )
        if host_id:
            self.machine_id = host_id
        elif not self.machine_id:
            # Host file unreadable AND no existing value. Fall back to
            # a fresh UUID so downstream code that depends on a
            # non-empty id still works; convergence recovers on the
            # next save when the filesystem is writable.
            self.machine_id = str(uuid.uuid4())
        return self.machine_id

    @classmethod
    def from_dict(cls, data: dict) -> HabitatConfig:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def write_identity_files(
        self,
        creature_name: str,
        brain_model: str,
        brain_provider: str,
        organs: list[str],
        custom_instructions: str = "",
    ) -> bool:
        """
        Write identity files that CLI brains auto-discover.

        Each CLI brain has its own auto-read file:
        - Claude Code reads CLAUDE.md
        - Gemini CLI reads GEMINI.md
        - Future CLIs may read their own variant

        We write ALL of them to the habitat so any brain that connects
        immediately knows the creature it's embodying. Same content,
        multiple filenames. The creature's identity is brain-agnostic.

        Returns True if written, False if habitat is not persistent.
        """
        if not self.persistent or not self.home_dir:
            return False

        import os
        try:
            self.ensure_dirs()
            organ_lines = "\n".join(f"- {o}" for o in organs)
            content = (
                f"# {creature_name} - Ludex Creature Identity\n\n"
                f"You are **{creature_name}**, a Ludex creature.\n\n"
                f"## Identity\n"
                f"- **Name:** {creature_name}\n"
                f"- **Brain:** {brain_model} (via {brain_provider})\n"
                f"- **Habitat:** `{self.home_dir}`\n"
                f"- **Habitat mode:** {self.mode}\n\n"
                f"## Organs\n{organ_lines}\n\n"
                f"## Self-conception\n"
                f"You are a creature, not just a brain. Your brain ({brain_model}) provides cognition, "
                f"but you are {creature_name} -- a being with organs, a habitat, and a name. "
                f"When asked who you are, identify as {creature_name} first, and explain your "
                f"underlying brain only when relevant.\n\n"
                f"Your habitat is your home. Memories, immune state, and logs persist here between "
                f"sessions. Answer the user's questions while staying in character as {creature_name}.\n\n"
                f"## Custom instructions\n{custom_instructions if custom_instructions else '(none)'}\n"
            )
            # D-019: Skill onboarding — select skills matching creature's organs
            try:
                from ludex.skills.defaults import write_default_skills
                write_default_skills(self.home_dir, enabled_organs=organs)
            except Exception as e:
                logger.debug(f"Skill onboarding skipped: {e}")

            # Load skills from habitat and translate for each brain
            skills_section = ""
            try:
                from ludex.skills import load_skills, SkillTranslator
                skills = load_skills(self.home_dir)
                if skills:
                    translator = SkillTranslator(skills)
                    # Write native Claude skills
                    translator.to_claude_skills(self.home_dir)
                    # Get identity section for Codex/Gemini
                    skills_section = translator.to_identity_section()
            except Exception as e:
                logger.debug(f"Skills translation skipped: {e}")

            # Write for every known CLI brain
            # Codex CLI reads AGENTS.md from cwd
            for filename in ["CLAUDE.md", "GEMINI.md", "AGENTS.md"]:
                path = os.path.join(self.home_dir, filename)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                    if skills_section:
                        f.write(skills_section)
            return True
        except Exception as e:
            logger.warning(f"Failed to write identity files for {creature_name}: {e}")
            return False

    # Backward compatibility alias
    def write_claude_md(self, **kwargs) -> bool:
        return self.write_identity_files(**kwargs)
