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
    "gemini_api": "GEMINI_API_KEY",
}

# Non-host-coupled baseline reasoning effort by provider (substrate axis E). claude/codex
# take a fixed level (model default = high, NOT a host's possibly-tuned xhigh); gemini/agy
# self-modulate (dynamic). Used by forge + re-brain so a creature's effort is its OWN, not
# silently inherited from the host CLI's settings. See the effort-substrate-axis note.
DEFAULT_EFFORT = {
    "claude_cli": "high", "codex_cli": "high", "grok_cli": "high",
    "gemini_cli": "dynamic", "agy_cli": "dynamic",
}


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
        "enabled": True,
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
    born_at: float = 0.0          # epoch timestamp of first creation (persists)
    session_count: int = 0        # how many times this creature has awakened
    # D-072: brain capability registry. Probed at first build (or when
    # brain identity changes). `capability_probed_brain` is the
    # "<provider>:<model>" key that produced the current set, so a
    # brain switch invalidates and re-probes.
    brain_capabilities: list = field(default_factory=list)
    capability_probed_brain: str = ""
    capability_probed_at: float = 0.0
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
        return cls(
            name=name,
            brain={"provider": provider, "model": model},
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

        if HAS_YAML:
            filepath = save_dir / "ludex.yaml"
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        else:
            filepath = save_dir / "ludex.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

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
            "born_at": self.born_at,
            "session_count": self.session_count,
        }
        if self.brain_capabilities or self.capability_probed_brain:
            d["brain_capabilities"] = list(self.brain_capabilities)
            d["capability_probed_brain"] = self.capability_probed_brain
            d["capability_probed_at"] = self.capability_probed_at
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
            born_at=data.get("born_at", 0.0),
            session_count=data.get("session_count", 0),
            brain_capabilities=list(data.get("brain_capabilities", [])),
            capability_probed_brain=data.get("capability_probed_brain", ""),
            capability_probed_at=data.get("capability_probed_at", 0.0),
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
        default_timeout = (
            240000 if provider_name in ("claude_sdk", "claude_cli", "gemini_cli", "codex_cli", "agy_cli")
            else 30000
        )
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
            cwd=provider_cwd,
            timeout_ms=provider_timeout,
            effort=self.brain.get("effort", ""),
            auth=self.brain.get("auth", ""),
        ))

        # Engine (required)
        if organ_cfgs.get("engine", {}).get("enabled", True):
            cfg = organ_cfgs["engine"]
            raw_prompt = cfg.get("system_prompt", "")
            # Inject temporal awareness into system prompt
            age_context = self._build_age_context(now)
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
                timeout_ms=30000,
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
            # Probe tool support for Ollama
            if provider_name == "ollama":
                from ludex.blocks.adapters.ollama import OllamaAdapter
                if not OllamaAdapter().supports_tools(model):
                    logger.debug(f"FC wiring skipped: {model} does not support tools")
                    return

            # Set up MCP context + get FC tools
            from ludex.mcp.ludex_mcp_server import create_ludex_mcp
            from ludex.mcp.function_calling import mcp_to_openai_tools, dispatch_tool_call_sync
            create_ludex_mcp(org)
            tools = mcp_to_openai_tools()

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
        parts.append(f"Current time: {local_now.strftime('%Y-%m-%d %H:%M')}.")

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
