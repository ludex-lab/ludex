"""
OrganismConfig — YAML 기반 에이전트 설정 저장/로드

ludex.yaml 형식:
  name: my-agent
  brain:
    provider: ollama
    model: llama3.1:8b
  organs:
    engine:
      enabled: true
      system_prompt: "..."
    memory:
      enabled: true
      auto_capture: false
    immune:
      enabled: true
      sensitivity: 1.0
    ...
  habitat:
    mode: local
    home_dir: ./
    max_storage_mb: 500
"""

from __future__ import annotations

import os
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ludex.core.habitat import HabitatConfig, HabitatMismatchError, get_host_habitat_origin

logger = logging.getLogger(__name__)

# Try YAML, fallback to JSON
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# Conventional env vars for BYO-key HTTP providers (fallback when brain.api_key unset).
_PROVIDER_ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini_api": "GEMINI_API_KEY",
}


def _default_provider_timeout_ms(provider_name: str) -> int:
    """Return the call timeout used when a brain does not pin one.

    Cursor cold starts are served behind a hosted CLI and need the full
    caretaker window. Other established CLI routes retain their historical
    four-minute default; HTTP/local routes retain 30 seconds.
    """

    if provider_name == "cursor_cli":
        return 400000
    if provider_name in (
        "claude_sdk", "claude_cli", "gemini_cli", "codex_cli", "agy_cli",
        "grok_cli",
    ):
        return 240000
    return 30000

# Non-host-coupled baseline reasoning effort by provider (substrate axis E). claude/codex
# take a fixed level (model default = high, NOT a host's possibly-tuned xhigh); gemini/agy
# self-modulate (dynamic). Used by forge + re-brain so a creature's effort is its OWN, not
# silently inherited from the host CLI's settings. See the effort-substrate-axis note.
# Effort is not free-form: some CLIs reject values the provider default happily
# produces. agy rejects `--effort dynamic` on an explicitly routed model —
# "invalid --effort \"dynamic\" (valid: low, medium, high)" — while DEFAULT_EFFORT
# hands agy_cli exactly that. The combination was latent until effort actually
# started reaching the CLI (2026-08-02), and then it failed at CALL time as an
# empty response, which is the silent shape this project keeps paying for.
#
# So the contract is checked where a creature is WRITTEN, not where it is called:
# an invalid pairing is a config error, and a config error should be impossible
# to save rather than discovered in a battery.
#
# Returns the allowed set, or None when we genuinely do not know — and "unknown"
# never blocks a save. A table like this rots (this file has now been wrong about
# agy twice), so it refuses only where a refusal is grounded in an observed CLI
# contract.
def effort_contract(provider: str, model: str) -> set[str] | None:
    """Allowed effort values for (provider, model), or None if unconstrained."""
    if provider == "agy_cli":
        # The flag is only sent for an explicitly routed model; the default model
        # takes no --effort at all, so "dynamic" stays legal there.
        from ludex.blocks.adapters.agy_cli import _PINNED_MODEL
        if model and model != _PINNED_MODEL:
            return {"low", "medium", "high"}
    if provider == "ollama":
        # A model's template serves the tiers it serves: qwen3.x has no "high",
        # and Ludex's common scale does. Refused where a creature is WRITTEN,
        # so an unserved tier is a config error rather than a warning at call
        # time. Source: 이음 (ludex-village) measurement, 2026-08-26. Models
        # not listed stay unconstrained — the table refuses only where the
        # refusal is grounded.
        from ludex.blocks.adapters.ollama import _THINK_TIERS
        for prefix, tiers in _THINK_TIERS.items():
            if model.startswith(prefix):
                return tiers
    if provider == "cursor_cli":
        # Effort is part of the wire id (kimi-k3-low …), so the allowed set IS
        # the id census — observed cursor-agent --list-models, 2026-08-18.
        # Families without effort-tier variants take no effort at all (an empty
        # set refuses any non-empty pin; unset stays legal).
        if model == "kimi-k3":
            return {"low", "high", "max"}
        if model == "glm-5.2":
            return {"high", "max"}
        if model in ("kimi-k2.7-code", "composer-2.5"):
            return set()
    return None


def _effort_order(values: set[str]) -> str:
    """Render an effort set in its ordinal order, not alphabetically.

    "high, low, medium" reads like a bug report about a bug report; effort is a
    scale, so it prints as one, and anything unrecognised follows sorted.
    """
    scale = ["low", "medium", "high", "xhigh", "max", "dynamic"]
    known = [v for v in scale if v in values]
    return ", ".join(known + sorted(values - set(known)))


class EffortContractError(ValueError):
    """Raised on save when (provider, model, effort) violates a known contract."""


DEFAULT_EFFORT = {
    "claude_cli": "high", "codex_cli": "high", "grok_cli": "high",
    "gemini_cli": "dynamic", "agy_cli": "dynamic", "cursor_cli": "high",
}

# KV-cache size for NEW ollama births. The server's own default is 262144,
# which loaded a 4B model at 12 GB where 32768 loaded it at 4.2 GB on the
# studio host — 7.8 GB from one field, and a 27B resident plus neighbours has
# to fit in the same habitat. Recommended by 이음 (ludex-village) 2026-08-26
# as a starting value, with anything past 65536 earning its way in on a
# long-context measurement rather than a default.
#
# Deliberately NOT applied to existing creatures: retrofitting a live brain's
# context window is a substrate change, and substrate changes go through the
# re-brain ritual, not through a constant appearing in a shared file.
DEFAULT_OLLAMA_NUM_CTX = 32768


# ============================================================
# Default organ configs
# ============================================================

DEFAULT_ORGANS = {
    "engine": {
        "enabled": True,
        "required": True,
        "system_prompt": "",
        "max_turns": 200,
        "token_budget": 100000,
    },
    "resilience": {
        "enabled": True,
        "required": True,
        "max_retries": 2,
        "initial_delay_ms": 1000,
        "max_delay_ms": 10000,
        "circuit_breaker_threshold": 10,
    },
    "memory": {
        "enabled": False,
        "auto_capture": True,
    },
    "immune": {
        "enabled": False,
        "sensitivity": 1.0,
        "autoregulate": True,
    },
    "humoral_immune": {
        "enabled": False,
        "activation_threshold": 2,
    },
    "emotion": {
        "enabled": False,
        "method": "behavioral",
        "full": False,
    },
    "tracking": {
        "enabled": False,
    },
    "hooks": {
        "enabled": False,
    },
    "auto": {
        # D-058 Phase A — interoceptive sense. Cheap (no LLM calls);
        # a defensive aggregator over sibling organ reads. Safe to
        # default-enable across creatures; when no sibling organs are
        # present the reading is neutral defaults.
        "enabled": True,
    },
    "chronos": {
        # D-059 Phase A — temporal sense. Derived from timestamps
        # (config.born_at, sensory tracker, SELF.md mtime). Cheap.
        "enabled": True,
    },
    "topos": {
        # D-060 Phase A — spatial / contextual sense. 4-layer nested
        # context (activity / locality / substrate / home). Cheap.
        "enabled": True,
    },
    "allos": {
        # D-061 Phase A — social sense. Scans bonds/ dir with
        # dir-mtime cache. Defensive on missing bonds dir.
        # Demoted to opt-in 2026-07-26 (JJ, default-review): no
        # main-loop consumer — bridges/arena setups enable explicitly;
        # re-promote when Phase B (present_others) lands a consumer.
        "enabled": False,
    },
    "physis": {
        # D-067 Physis — field-dynamics world model (both consolidation modes live).
        # Cheap when idle (only loads/writes when fields call its
        # ports). See docs/field-indexed-world-models-design.md.
        "enabled": True,
    },
    "sphygmos": {
        # Sphygmos — vitals/reflex/adaptive immune memory
        # (docs/sphygmos-organ-design.md, decisions locked 2026-07-10).
        # PILOT rollout: default OFF; enabled per-creature (Kiln first)
        # until the pilot proves the autoimmune metric in the wild.
        # Cheap: no LLM calls; passive unless its ports are fed.
        "enabled": False,
    },
    "taxis": {
        # Taxis — planning/sequencing control organ
        # (docs/taxis-organ-design.md; the v3-validated commit-latch gate
        # as a carried faculty). Default OFF and NOT creature-enabled yet:
        # Taxis alters behavior (directives into prompts), so wild enable
        # is gated on the pre-registered 2x2 (P1/P2), unlike sphygmos
        # whose gate was the offline battery alone.
        "enabled": False,
    },
}

PRESETS = {
    "full": {k: {**v, "enabled": True} for k, v in DEFAULT_ORGANS.items()},
    "minimal": {k: {**v, "enabled": v.get("required", False)} for k, v in DEFAULT_ORGANS.items()},
    "secure": {
        **{k: {**v, "enabled": v.get("required", False)} for k, v in DEFAULT_ORGANS.items()},
        # memory: humoral immunity *learns* threat patterns across interactions,
        # which needs persistence — a secure creature should remember (JJ 2026-06-07).
        "memory": {**DEFAULT_ORGANS["memory"], "enabled": True},
        "immune": {**DEFAULT_ORGANS["immune"], "enabled": True},
        "humoral_immune": {**DEFAULT_ORGANS["humoral_immune"], "enabled": True},
    },
    "social": {
        **{k: {**v, "enabled": v.get("required", False)} for k, v in DEFAULT_ORGANS.items()},
        "emotion": {**DEFAULT_ORGANS["emotion"], "enabled": True},
        "memory": {**DEFAULT_ORGANS["memory"], "enabled": True},
    },
}


# ============================================================
# OrganismConfig
# ============================================================

@dataclass
class OrganismConfig:
    """에이전트 전체 설정."""
    name: str = "agent"
    brain: dict = field(default_factory=lambda: {"provider": "ollama", "model": "llama3.1:8b"})
    organs: dict = field(default_factory=lambda: dict(DEFAULT_ORGANS))
    habitat: HabitatConfig = field(default_factory=HabitatConfig.temporary)
    # What this body IS in the ecosystem, as distinct from what its brain is
    # like (brain.class) — a measurement base and a creature need the same
    # organs but not the same care. "probe" bodies are instruments: their value
    # is staying blank, because a base that accumulates memories changes the
    # recall surface every battery copies and quietly moves the measurement.
    # Being USED as a base does not make a creature an instrument; only being
    # forged as one does (see AgyProbe's SELF.md).
    role: str = "creature"        # creature | probe
    born_at: float = 0.0          # epoch timestamp of first creation (persists)
    session_count: int = 0        # how many times this creature has awakened
    # D-072: brain capability registry. Probed at first build (or when
    # brain identity changes). `capability_probed_brain` is the
    # "<provider>:<model>" key that produced the current set, so a
    # brain switch invalidates and re-probes.
    brain_capabilities: list = field(default_factory=list)
    capability_probed_brain: str = ""
    capability_probed_at: float = 0.0
    # FC-wiring tool-support probe cache. supports_tools() is a REAL
    # /api/chat call, and for an ollama brain that call loads the entire
    # model into RAM — re-probing on every build loaded Moss's 9.6GB
    # gemma4 at each heartbeat for a month, and on 2026-08-24 02:03
    # (1.9GB free, 0 swap) took down the habitat's whole GUI session.
    # The answer is a constant per brain identity, so probe once and
    # persist; same invalidation key idiom as capability_probed_brain.
    fc_probed_brain: str = ""
    fc_supports_tools: bool = False
    # When True, save() refuses to write to disk. Set by callers that
    # mutate brain identity in-memory for ephemeral use (the MCP server
    # without --enable-engine is the canonical case). Without this guard,
    # build()'s session-count save and the D-072 probe save would persist
    # the in-memory mutation and corrupt the on-disk creature config.
    # Cody (Mac) hit this 2026-05-11; Verse's brain block was rewritten
    # to ollama:none and engine.enabled flipped to false on disk, leaving
    # six empty turns in a downstream OpenCouncil session.
    _ephemeral: bool = field(default=False, repr=False, compare=False)

    @classmethod
    def from_preset(cls, preset: str = "full", name: str = "agent",
                    model: str = "llama3.1:8b", provider: str = "ollama") -> OrganismConfig:
        """프리셋으로 생성."""
        organs = PRESETS.get(preset, PRESETS["full"])
        brain: dict = {"provider": provider, "model": model}
        # A new ollama body gets its context window written down rather than
        # inherited from whatever the server happens to default to. Existing
        # creatures are untouched — see DEFAULT_OLLAMA_NUM_CTX.
        if provider == "ollama":
            brain["num_ctx"] = DEFAULT_OLLAMA_NUM_CTX
        return cls(
            name=name,
            brain=brain,
            organs={k: dict(v) for k, v in organs.items()},
        )

    def enable_organ(self, organ: str, **kwargs):
        """장기 활성화 + 파라미터 설정."""
        if organ in self.organs:
            self.organs[organ]["enabled"] = True
            self.organs[organ].update(kwargs)
        else:
            self.organs[organ] = {"enabled": True, **kwargs}

    def disable_organ(self, organ: str):
        """장기 비활성화."""
        if organ in self.organs:
            self.organs[organ]["enabled"] = False

    def get_enabled_organs(self) -> list[str]:
        """활성화된 장기 목록."""
        return [k for k, v in self.organs.items() if v.get("enabled", False)]

    @property
    def brain_class(self) -> str:
        """Brain class — narrative / structured / hybrid / unknown.
        Reads `brain.class` if explicitly set in ludex.yaml; otherwise
        derives from `(provider, model)` via `classify_brain`. Coarse
        routing class for *what fields a creature can run* (distinct
        from `brain_capabilities` fine-grained probing). See
        `ludex/core/brain_class.py` for the taxonomy."""
        from ludex.core.brain_class import classify_brain
        explicit = (self.brain or {}).get("class", "")
        if explicit:
            return str(explicit)
        return classify_brain(
            (self.brain or {}).get("provider", ""),
            (self.brain or {}).get("model", ""),
        )

    def check_canonical_host(self) -> tuple[bool, str]:
        """Compare this creature's `habitat.origin` against the host-level
        habitat-origin marker. Returns `(ok, message)` without raising;
        callers decide whether to refuse activation. The guard is silently
        skipped when either side is empty or `persistent=False`, so it
        never breaks creatures that haven't opted in.

        Set the host marker via `$LUDEX_HABITAT_ORIGIN` or
        `~/.ludex/habitat_origin` (e.g. "Ray-habitat" / "Mac-habitat").
        """
        if not self.habitat.persistent:
            return True, "persistent=False; guard skipped"
        creature_origin = (self.habitat.origin or "").strip()
        host_origin = get_host_habitat_origin()
        if not creature_origin or not host_origin:
            return True, "origin unset on creature or host; guard skipped"
        if creature_origin != host_origin:
            return False, (
                f"creature origin '{creature_origin}' does not match host "
                f"origin '{host_origin}' — refusing to build a persistent "
                f"creature on a foreign host. If this is intentional, clear "
                f"or change the host marker (LUDEX_HABITAT_ORIGIN env var or "
                f"~/.ludex/habitat_origin); to inspect without building, use "
                f"OrganismConfig.load() and don't call .build()."
            )
        return True, "origins match"

    # ============================================================
    # Save / Load
    # ============================================================

    def save(self, path: str = ""):
        """설정을 YAML (또는 JSON) 파일로 저장."""
        if self._ephemeral:
            # Caller has marked this config as ephemeral (in-memory
            # mutation only). Refusing to persist prevents the MCP-
            # server-style brain-rewrite from clobbering the on-disk
            # ludex.yaml. See _ephemeral docstring for the failure mode
            # this guards against.
            logger.debug("save() skipped: config marked _ephemeral")
            return
        provider = self.brain.get("provider", "")
        model = self.brain.get("model", "")
        effort = self.brain.get("effort", "")
        allowed = effort_contract(provider, model)
        if allowed is not None and effort and effort not in allowed:
            raise EffortContractError(
                f"{provider} rejects effort {effort!r} for model {model!r} "
                f"(allowed: {_effort_order(allowed)}). Refusing to save a "
                f"creature whose every brain call would fail — fix brain.effort "
                f"in the forge/rebrain step rather than discovering this at call "
                f"time as an empty response.")

        save_dir = path or self.habitat.home_dir
        if not save_dir:
            logger.warning("No save path specified and no habitat home_dir")
            return

        # D-060: ensure machine_id is populated before write. First-save
        # creatures get a fresh UUID; subsequent saves preserve it.
        self.habitat.ensure_machine_id()

        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        data = self.to_dict()
        # Keep committed JSON portable: when habitat.home_dir equals the
        # directory we're saving into (the normal case — creature lives
        # where its config lives), serialize home_dir as "." so the file
        # is cwd-independent across machines and checkouts.
        try:
            if self.habitat.home_dir and Path(self.habitat.home_dir).resolve() == save_dir.resolve():
                data["habitat"]["home_dir"] = "."
        except (OSError, ValueError):
            pass

        # Atomic write: serialize to a temp file in the same dir, then
        # os.replace onto the target. A plain open("w") is not atomic, so two
        # concurrent saves (e.g. capability re-probe during a PARALLEL build of
        # several re-brained creatures) interleave and split a value mid-line —
        # observed 2026-08-15: Comet/Nova/Flare's capability_probed_at float got
        # a newline inserted before its last digit, breaking YAML load. Same-dir
        # temp + os.replace makes each save all-or-nothing.
        import os as _os
        import tempfile as _tf
        if HAS_YAML:
            filepath = save_dir / "ludex.yaml"
            text = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        else:
            filepath = save_dir / "ludex.json"
            text = json.dumps(data, indent=2, ensure_ascii=False)
        fd, tmp = _tf.mkstemp(dir=str(save_dir), prefix=".ludex_", suffix=".tmp")
        try:
            with _os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            _os.replace(tmp, filepath)
        except BaseException:
            try:
                _os.unlink(tmp)
            except OSError:
                pass
            raise

        logger.info(f"Config saved to {filepath}")
        return str(filepath)

    @classmethod
    def load(cls, path: str) -> OrganismConfig:
        """YAML 또는 JSON에서 설정 로드."""
        path = Path(path)

        # 디렉토리면 ludex.yaml/json 찾기
        if path.is_dir():
            if (path / "ludex.yaml").exists():
                path = path / "ludex.yaml"
            elif (path / "ludex.json").exists():
                path = path / "ludex.json"
            else:
                raise FileNotFoundError(f"No ludex.yaml or ludex.json in {path}")

        with open(path, "r", encoding="utf-8") as f:
            if path.suffix in (".yaml", ".yml"):
                if not HAS_YAML:
                    raise ImportError("PyYAML required: pip install pyyaml")
                data = yaml.safe_load(f)
            else:
                data = json.load(f)

        cfg = cls.from_dict(data)
        # A creature's home is the directory containing its config.
        # Overwrite habitat.home_dir with that absolute path so downstream
        # consumers (memory, store, provider cwd, skill writers) don't
        # resolve against the caller's cwd — which breaks when load() is
        # invoked from outside the Ludex checkout (e.g. the LxM adapter).
        cfg.habitat.home_dir = str(path.parent.resolve())
        return cfg

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "brain": self.brain,
            "organs": self.organs,
            "habitat": self.habitat.to_dict(),
            "role": self.role,
            "born_at": self.born_at,
            "session_count": self.session_count,
        }
        if self.brain_capabilities or self.capability_probed_brain:
            d["brain_capabilities"] = list(self.brain_capabilities)
            d["capability_probed_brain"] = self.capability_probed_brain
            d["capability_probed_at"] = self.capability_probed_at
        if self.fc_probed_brain:
            d["fc_probed_brain"] = self.fc_probed_brain
            d["fc_supports_tools"] = bool(self.fc_supports_tools)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> OrganismConfig:
        habitat_data = data.get("habitat", {})
        habitat = HabitatConfig.from_dict(habitat_data) if habitat_data else HabitatConfig.temporary()

        # Merge with defaults for missing organs.
        # Deep-copy the inner dicts so subsequent loads cannot mutate the
        # module-level DEFAULT_ORGANS (which would bleed across creatures
        # loaded in the same Python process).
        organs = {k: dict(v) for k, v in DEFAULT_ORGANS.items()}
        for k, v in data.get("organs", {}).items():
            if k in organs:
                organs[k].update(v)
            else:
                organs[k] = dict(v) if isinstance(v, dict) else v

        return cls(
            name=data.get("name", "agent"),
            brain=data.get("brain", {"provider": "ollama", "model": "llama3.1:8b"}),
            organs=organs,
            habitat=habitat,
            role=data.get("role", "creature"),
            born_at=data.get("born_at", 0.0),
            session_count=data.get("session_count", 0),
            brain_capabilities=list(data.get("brain_capabilities", [])),
            capability_probed_brain=data.get("capability_probed_brain", ""),
            capability_probed_at=data.get("capability_probed_at", 0.0),
            fc_probed_brain=data.get("fc_probed_brain", ""),
            fc_supports_tools=bool(data.get("fc_supports_tools", False)),
        )

    # ============================================================
    # Build Organism
    # ============================================================

    def build(self):
        """설정으로부터 Organism 조립."""
        from ludex.core.organism import Organism
        from ludex.blocks.provider import ProviderBlock
        from ludex.blocks.engine import EngineBlock
        from ludex.blocks.resilience import ResilienceBlock
        from ludex.blocks.tracking import TrackingBlock
        from ludex.blocks.hooks import HooksBlock
        from ludex.blocks.memory import MemoryBlock
        from ludex.blocks.immune import ImmuneBlock
        from ludex.blocks.humoral_immune import HumoralImmuneBlock
        from ludex.blocks.emotion import EmotionBlock
        from ludex.blocks.auto import AutoBlock
        from ludex.blocks.chronos import ChronosBlock
        from ludex.blocks.topos import ToposBlock
        from ludex.blocks.allos import AllosBlock
        from ludex.blocks.physis import PhysisBlock

        import time as _time

        blocks = []
        organ_cfgs = self.organs

        # Canonical-host guard (D-052 sovereignty + smoke_016 reproducibility
        # protection). Run before any state mutation so a foreign-host
        # attempt fails clean without bumping session_count or rewriting
        # machine_id. Opt-in: when the host marker is unset the guard
        # is silently skipped.
        ok, msg = self.check_canonical_host()
        if not ok:
            raise HabitatMismatchError(msg)

        # Track birth and sessions
        now = _time.time()
        if self.born_at == 0.0:
            # First birth
            self.born_at = now
            self.session_count = 1
        else:
            # Resuming — increment session count
            self.session_count += 1

        # Auto-save updated birth/session info
        if self.habitat.persistent and self.habitat.home_dir:
            try:
                self.save()
            except Exception:
                pass

        # Provider (always included)
        # Pass habitat path as cwd so the brain "lives" in its habitat
        # (relevant for subprocess-based adapters like claude_cli, claude_sdk)
        provider_cwd = ""
        if self.habitat.persistent and self.habitat.home_dir:
            from pathlib import Path
            try:
                provider_cwd = str(Path(self.habitat.home_dir).resolve())
            except Exception:
                provider_cwd = self.habitat.home_dir
        provider_name = self.brain.get("provider", "ollama")
        # CLI-based brains need longer timeout — subprocess startup + model init.
        # Per-brain `timeout_ms` in ludex.yaml's `brain` block overrides the
        # default. Added 2026-05-10 (D-080 follow-up): gemini-3.1-pro-preview
        # latency on narrative tasks routinely exceeds 240s; raising Wick's
        # timeout via config rather than bumping the global default for all
        # CLI brains keeps the change scoped to the creature that actually
        # needs it.
        # grok_cli added 2026-08-01: it was missing from this list since the
        # provider landed (07-13), so every grok creature silently ran on the
        # 30s non-CLI default while the grok adapter's own signature default is
        # 120s — unreachable. grok-4.5 is a reasoning brain and routinely needs
        # longer; the truncation surfaced as an EMPTY response, indistinguishable
        # from "the brain had nothing to say". Found when a physics battery got
        # empty output from every grok call.
        # Cursor was accidentally omitted from the CLI set, so ProviderBlock's
        # 30s default overrode CursorCliAdapter's own 240s signature. A hosted
        # Kimi cold start then timed out twice with zero bytes before the
        # caretaker's 300–400s wait policy could matter. Give Cursor one full
        # cold-start window; keep established defaults for the other routes.
        default_timeout = _default_provider_timeout_ms(provider_name)
        provider_timeout = int(self.brain.get("timeout_ms", default_timeout))
        # BYO-key (HTTP adapters): explicit brain.api_key wins, else fall back to
        # the provider's conventional env var. CLI brains auth via their own login
        # (os.environ passed to subprocess) and ignore this.
        api_key = self.brain.get("api_key", "") or os.getenv(
            _PROVIDER_ENV_KEYS.get(provider_name, ""), ""
        )
        blocks.append(ProviderBlock(
            provider=provider_name,
            model=self.brain.get("model", "llama3.1:8b"),
            base_url=self.brain.get("base_url", ""),
            api_key=api_key,
            # Per-brain generation envelope. These fields existed on
            # ProviderBlock but build() never forwarded them, so changing a
            # creature's max_tokens had no effect and a local reasoning model
            # silently kept the 4096-token default.
            temperature=float(self.brain.get("temperature", 0.7)),
            max_tokens=int(self.brain.get("max_tokens", 4096)),
            cwd=provider_cwd,
            timeout_ms=provider_timeout,
            effort=self.brain.get("effort", ""),
            auth=self.brain.get("auth", ""),
            # ollama KV-cache size (habitat cost). Absent = server default,
            # which is what every pre-2026-08-26 creature has been running on;
            # new ollama births carry DEFAULT_OLLAMA_NUM_CTX explicitly.
            num_ctx=self.brain.get("num_ctx", None),
            # Ruling No. 3 write jurisdiction — opt-in, empty by default. The
            # village layer (reveille) sets brain["write_dirs"] before build to
            # grant a claude resident write access to its own desk + owned cards.
            write_dirs=self.brain.get("write_dirs", []),
        ))

        # Engine (required)
        if organ_cfgs.get("engine", {}).get("enabled", True):
            cfg = organ_cfgs["engine"]
            raw_prompt = cfg.get("system_prompt", "")
            # Inject temporal awareness into system prompt.
            # agecontext.clock surface toggle (registry row, ae8b860):
            # measurement arms set organs.engine.agecontext_clock=False on
            # the ephemeral copy (same pattern as organ arm-toggles).
            age_context = self._build_age_context(now) \
                if cfg.get("agecontext_clock", True) else ""
            if age_context:
                raw_prompt = raw_prompt.rstrip() + "\n\n" + age_context if raw_prompt else age_context
            # P4: adapt system prompt to brain characteristics
            try:
                from ludex.core.prompt_templates import adapt_system_prompt
                adapted_prompt = adapt_system_prompt(
                    prompt=raw_prompt,
                    provider=provider_name,
                    model=self.brain.get("model", ""),
                    creature_name=self.name,
                    organs=self.get_enabled_organs(),
                )
            except Exception:
                adapted_prompt = raw_prompt
            blocks.append(EngineBlock(
                max_turns=cfg.get("max_turns", 200),
                token_budget=cfg.get("token_budget", 100000),
                system_prompt=adapted_prompt,
                recall_delivery=cfg.get("recall_delivery", "system"),
            ))

        # Resilience (required)
        if organ_cfgs.get("resilience", {}).get("enabled", True):
            cfg = organ_cfgs["resilience"]
            blocks.append(ResilienceBlock(
                max_retries=cfg.get("max_retries", 2),
                initial_delay_ms=cfg.get("initial_delay_ms", 1000),
                max_delay_ms=cfg.get("max_delay_ms", 10000),
                circuit_breaker_threshold=cfg.get("circuit_breaker_threshold", 10),
            ))

        # Memory
        if organ_cfgs.get("memory", {}).get("enabled", False):
            cfg = organ_cfgs["memory"]
            storage_dir = self.habitat.get_path("memory") or ""
            blocks.append(MemoryBlock(
                storage_dir=storage_dir,
                auto_capture=cfg.get("auto_capture", True),
            ))

        # Tracking
        if organ_cfgs.get("tracking", {}).get("enabled", False):
            blocks.append(TrackingBlock(experiment_name=self.name))

        # Hooks
        if organ_cfgs.get("hooks", {}).get("enabled", False):
            blocks.append(HooksBlock())

        # Immune (Cellular)
        if organ_cfgs.get("immune", {}).get("enabled", False):
            cfg = organ_cfgs["immune"]
            blocks.append(ImmuneBlock(
                sensitivity=cfg.get("sensitivity", 1.0),
                autoregulate=cfg.get("autoregulate", True),
            ))

        # Humoral Immune
        if organ_cfgs.get("humoral_immune", {}).get("enabled", False):
            cfg = organ_cfgs["humoral_immune"]
            blocks.append(HumoralImmuneBlock(
                activation_threshold=cfg.get("activation_threshold", 2),
            ))

        # Emotion
        if organ_cfgs.get("emotion", {}).get("enabled", False):
            cfg = organ_cfgs["emotion"]
            blocks.append(EmotionBlock(
                method=cfg.get("method", "behavioral"),
                full=cfg.get("full", False),
            ))

        # Auto (D-058 Phase A) — interoceptive sense. Aggregator over
        # sibling organs; holds no state. Default-enabled but opt-out
        # via config is fine for capacity-constrained creatures.
        if organ_cfgs.get("auto", {}).get("enabled", True):
            blocks.append(AutoBlock())

        # Chronos (D-059 Phase A) — temporal sense.
        if organ_cfgs.get("chronos", {}).get("enabled", True):
            blocks.append(ChronosBlock())

        # Topos (D-060 Phase A) — contextual / spatial sense.
        if organ_cfgs.get("topos", {}).get("enabled", True):
            blocks.append(ToposBlock())

        # Allos (D-061 Phase A) — social sense (known-others bonds).
        if organ_cfgs.get("allos", {}).get("enabled", True):
            blocks.append(AllosBlock())

        # Physis (D-067) — field-dynamics world model (both consolidation modes live).
        # Default-enabled; fields/orchestrators that don't use physis
        # can leave it idle (no traces appended → no consolidation).
        if organ_cfgs.get("physis", {}).get("enabled", True):
            blocks.append(PhysisBlock())

        # Sphygmos — vitals/reflex/adaptive immune memory. PILOT: default OFF.
        if organ_cfgs.get("sphygmos", {}).get("enabled", False):
            from ludex.blocks.sphygmos import SphygmosBlock
            blocks.append(SphygmosBlock())

        # Taxis — planning/sequencing control organ. Default OFF; wild enable
        # gated on the pre-registered 2x2 (behavior-altering, see DEFAULT_ORGANS).
        if organ_cfgs.get("taxis", {}).get("enabled", False):
            from ludex.blocks.taxis import TaxisBlock
            blocks.append(TaxisBlock())

        # Ensure habitat dirs
        self.habitat.ensure_dirs()

        # Build organism
        org = Organism(
            blocks=blocks,
            name=self.name,
            config={
                "model": self.brain.get("model", ""),
                "provider": self.brain.get("provider", ""),
                "habitat_dir": self.habitat.home_dir or "",
                "habitat_mode": self.habitat.mode,
                "max_storage_mb": self.habitat.max_storage_mb,
                # D-052 habitat identity + D-060 machine identity — mirrored
                # so organs (Topos especially) can read without reaching
                # back into HabitatConfig.
                "habitat_origin": self.habitat.origin or "",
                "machine_id": self.habitat.machine_id or "",
                "machine_alias": self.habitat.machine_alias or "",
                # Born + session are useful for Chronos reads.
                "born_at": float(self.born_at or 0.0),
                "session_count": int(self.session_count or 0),
            },
        )

        # Phase 5e: Auto-wire FC tools for Ollama/OpenAI brains
        self._wire_function_calling(org, provider_name)

        # D-072 Phase A: brain capability probe. Runs lazily — only
        # the first time a creature is built with a given brain (or
        # when the brain identity changes). Result is cached on the
        # config and persisted to ludex.yaml. Capped at 30s wall-clock;
        # on failure / timeout the creature simply has an empty
        # capability set and field-level adapters route accordingly.
        self._maybe_probe_brain_capabilities(provider_name, provider_cwd)

        return org

    def _maybe_probe_brain_capabilities(self, provider_name: str, provider_cwd: str):
        """D-072 Phase A pillar 1. Idempotent across reloads."""
        model = self.brain.get("model", "")
        brain_key = f"{provider_name}:{model}"
        if self.brain_capabilities and self.capability_probed_brain == brain_key:
            return  # already probed this exact brain identity
        try:
            from ludex.core.birth_probe import (
                probe_brain_capabilities, capability_set,
            )
            snapshot = probe_brain_capabilities(
                provider_name=provider_name,
                model=model,
                cwd=provider_cwd,
                # The creature's own ceiling, not a flat 30s. A reasoning brain
                # takes minutes, so a hardcoded 30s probe times out and the
                # creature is born with an empty capability set — which is what
                # happened to the first grok creature. Same failure the CLI
                # timeout list had: a number chosen for fast brains applied to
                # every brain. Capped so a slow probe cannot stall a build for
                # the full battery ceiling.
                timeout_ms=min(int(self.brain.get("timeout_ms", 0) or 30000),
                               120000),
                effort=self.brain.get("effort", ""),
                num_ctx=self.brain.get("num_ctx"),
            )
            self.brain_capabilities = capability_set(snapshot)
            self.capability_probed_brain = brain_key
            self.capability_probed_at = snapshot.get("probed_at", 0.0)
            logger.info(
                f"D-072 probe {brain_key}: caps={self.brain_capabilities} "
                f"elapsed_ms={snapshot.get('elapsed_ms')} "
                f"error={snapshot.get('error', '')!r}"
            )
            try:
                self.save()
            except Exception as e:
                logger.debug(f"D-072 probe save failed: {e}")
        except Exception as e:
            logger.debug(f"D-072 probe skipped: {type(e).__name__}: {e}")

    def _wire_function_calling(self, org, provider_name: str):
        """Auto-wire organ tools for function-calling-capable brains.

        Phase 5e: Ollama/OpenAI brains that support function calling get
        organ tools wired automatically. The Engine's default tools and
        dispatcher are set so handle_submit(prompt) works without manual wiring.

        For Claude CLI/SDK, MCP is used instead (already wired in adapter).
        For Gemini CLI, prompt-only is used (no FC support).
        """
        if provider_name not in ("ollama", "openai", "anthropic"):
            return  # CLI brains use MCP or prompt-only

        model = self.brain.get("model", "")
        try:
            # Probe tool support for Ollama — once per brain identity. The
            # probe is a real generate call (= full model load), so a cached
            # verdict is used whenever the brain hasn't changed.
            if provider_name == "ollama":
                brain_key = f"{provider_name}:{model}"
                if self.fc_probed_brain == brain_key:
                    supported = self.fc_supports_tools
                else:
                    from ludex.blocks.adapters.ollama import OllamaAdapter
                    # The creature's own context window, not the server's
                    # default: this probe is often a body's FIRST contact
                    # with its brain, so whatever it loads is what the
                    # habitat pays for before a single configured call runs.
                    supported = OllamaAdapter().supports_tools(
                        model, num_ctx=self.brain.get("num_ctx"))
                    self.fc_probed_brain = brain_key
                    self.fc_supports_tools = supported
                    try:
                        self.save()
                    except Exception as e:
                        logger.debug(f"FC probe cache save failed: {e}")
                if not supported:
                    logger.debug(f"FC wiring skipped: {model} does not support tools")
                    return

            # Bind the provider-neutral organ registry directly. Constructing
            # a Claude SDK MCP server here made an optional SDK a hidden
            # dependency of local Ollama/OpenAI function calling.
            from ludex.mcp.ludex_mcp_server import (
                bind_ludex_organism,
                select_ludex_tools,
            )
            from ludex.mcp.function_calling import mcp_to_openai_tools, dispatch_tool_call_sync
            bind_ludex_organism(org)
            selected = select_ludex_tools(org, include_engine=False)
            tools = mcp_to_openai_tools([t.name for t in selected])

            if not tools:
                return

            # Store on organism for Engine to pick up
            org._fc_tools = tools
            org._fc_dispatcher = dispatch_tool_call_sync

            # Wire into Engine: set default tools so handle_submit() auto-uses them
            engine = org.get_block("engine")
            if engine:
                engine._default_tools = tools
                engine._default_tool_dispatcher = dispatch_tool_call_sync

            logger.info(f"FC wiring complete: {len(tools)} organ tools for {provider_name}:{model}")
        except Exception as e:
            logger.debug(f"FC wiring failed for {provider_name}:{model}: {e}")

    def _build_age_context(self, now: float) -> str:
        """Build temporal awareness context for system prompt.

        Gives the creature a sense of time: when it was born, how old it is,
        how many times it has awakened, and the current local time.
        """
        import time as _time
        from datetime import datetime

        parts = []
        local_now = datetime.fromtimestamp(now)
        # Label fix (Ray re-review ae8b860): this stamp is the BUILD moment,
        # frozen for the whole session — "Current time" mislabeled it and
        # hid the channel (the grok probe fell into exactly that trap).
        parts.append(f"Session started: {local_now.strftime('%Y-%m-%d %H:%M')}.")

        if self.born_at > 0:
            born_dt = datetime.fromtimestamp(self.born_at)
            age_seconds = now - self.born_at

            if age_seconds < 60:
                age_str = "just born"
            elif age_seconds < 3600:
                age_str = f"{int(age_seconds / 60)} minutes old"
            elif age_seconds < 86400:
                age_str = f"{age_seconds / 3600:.1f} hours old"
            else:
                age_str = f"{age_seconds / 86400:.1f} days old"

            parts.append(f"Born: {born_dt.strftime('%Y-%m-%d %H:%M')}. Age: {age_str}.")
            parts.append(f"This is awakening #{self.session_count}.")

        return " ".join(parts)
