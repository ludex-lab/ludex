"""
Ludex Web Demo — "Connect a Brain, Get a Creature"

Minimal web interface for Ludex agent demos.
Connect an LLM (Ollama/API key) and interact with a fully assembled organism.

Run:
    python web/server.py
    python web/server.py --port 8080
"""

import sys
import os
import json
import time
import asyncio
import argparse
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ludex.core.organism import Organism
from ludex.core.organism_config import OrganismConfig, DEFAULT_ORGANS, PRESETS
from ludex.core.habitat import HabitatConfig
from ludex.blocks.provider import ProviderBlock
from ludex.blocks.engine import EngineBlock
from ludex.blocks.resilience import ResilienceBlock
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.hooks import HooksBlock
from ludex.blocks.immune import ImmuneBlock
from ludex.blocks.humoral_immune import HumoralImmuneBlock
from ludex.blocks.emotion import EmotionBlock
from ludex.blocks.memory import MemoryBlock


# ============================================================
# State
# ============================================================

agents: dict[str, Organism] = {}
agent_types: dict[str, str] = {}


# ============================================================
# Agent Builder
# ============================================================

CHAT_PROMPT = (
    "You are a helpful, friendly assistant. "
    "Be concise but warm. If you notice the user seems upset, "
    "acknowledge their feelings before helping."
)

RAG_PROMPT = (
    "You are a knowledgeable assistant with access to a document memory. "
    "When answering questions, use the recalled context provided to you. "
    "If the context contains relevant information, cite it. "
    "If the context doesn't cover the question, say so honestly. Be concise."
)


def build_agent(session_id: str, model: str, provider: str = "ollama",
                system_prompt: str = "", agent_type: str = "chat") -> Organism:
    """Assemble an organism based on agent type."""
    if agent_type == "rag":
        default_prompt = RAG_PROMPT
    else:
        default_prompt = CHAT_PROMPT

    blocks = [
        ProviderBlock(provider=provider, model=model),
        EngineBlock(
            max_turns=200,
            token_budget=100000,
            system_prompt=system_prompt or default_prompt,
        ),
        ResilienceBlock(max_retries=2, initial_delay_ms=1000, max_delay_ms=10000,
                       circuit_breaker_threshold=10),
        TrackingBlock(experiment_name=f"web-{session_id}"),
        HooksBlock(),
        ImmuneBlock(sensitivity=1.0, autoregulate=True),
        HumoralImmuneBlock(activation_threshold=3),
        EmotionBlock(method="behavioral"),
    ]

    if agent_type == "rag":
        blocks.append(MemoryBlock(
            storage_dir=f"./ludex_web_memory/{session_id}",
            auto_capture=False,
        ))

    org = Organism(
        blocks=blocks,
        name=f"{agent_type}-{session_id}",
        config={"model": model, "provider": provider},
    )
    agents[session_id] = org
    agent_types[session_id] = agent_type
    return org


def get_vitals(org: Organism) -> dict:
    """Collect all vitals from organism."""
    vitals = org.measure_vitals()

    # Emotion
    emo_block = org.get_block("emotion")
    emo_state = emo_block.handle_get_emotional_state() if emo_block else {}

    # Cellular Immune
    immune = org.get_block("immune")
    immune_status = immune.handle_get_immune_status() if immune else None

    # Humoral Immune
    humoral = org.get_block("humoral_immune")
    humoral_status = humoral.handle_get_humoral_status() if humoral else None

    # Tracking
    tracking = org.get_block("tracking")
    report = tracking.handle_get_report() if tracking else {}

    return {
        "vitals": {
            "total_turns": vitals.total_turns,
            "tokens_per_turn": round(vitals.tokens_per_turn, 1),
            "error_rate": round(vitals.error_rate, 3),
        },
        "emotion": {
            "valence": round(emo_state.get("current", {}).get("valence", 0), 3),
            "arousal": round(emo_state.get("current", {}).get("arousal", 0), 3),
            "desperation": round(emo_state.get("current", {}).get("desperation", 0), 3),
            "calm": round(emo_state.get("current", {}).get("calm", 0), 3),
            "dominant": emo_state.get("current", {}).get("dominant_emotion", "neutral"),
        },
        "immune": {
            "threat": round(immune_status.threat_level, 3) if immune_status else 0,
            "desperation": round(immune_status.desperation_signal, 3) if immune_status else 0,
            "calm": round(immune_status.calm_signal, 3) if immune_status else 1,
            "sensitivity": round(immune_status.sensitivity, 2) if immune_status else 1,
            "interventions": immune_status.total_interventions if immune_status else 0,
        },
        "humoral": {
            "memory_cells": humoral_status.memory_cells if humoral_status else 0,
            "antibodies": humoral_status.active_antibodies if humoral_status else 0,
            "threat": round(humoral_status.threat_level, 3) if humoral_status else 0,
            "exploitation": round(humoral_status.exploitation_score, 1) if humoral_status else 0,
        },
        "tracking": {
            "total_tokens": report.get("total_tokens", 0),
            "avg_latency": round(report.get("avg_latency_ms", 0), 0),
            "total_errors": report.get("total_errors", 0),
        },
    }


# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(title="Ludex Demo")
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


# ---- self-update (git-based) --------------------------------------------------
# The local app is a git clone of ludex-lab/ludex; users would otherwise sit on
# stale code forever. These let the client detect "you're behind" and apply it
# with one click (git pull, then reload or self-restart).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GH_REPO = "ludex-lab/ludex"
_START_TS = time.time()          # changes on every (re)start — lets the client know a restart finished
_UPDATE_TTL = 3600               # cache the GitHub check (unauthenticated rate limit is 60/hr)
_update_cache = {"ts": 0.0, "data": None}


def _git(*args, timeout=60):
    import subprocess
    return subprocess.run(["git", "-C", REPO_ROOT, *args], capture_output=True, text=True, timeout=timeout)


def _local_head():
    r = _git("rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


@app.get("/api/version")
async def version():
    sha = _local_head()
    return {"sha": (sha or "")[:7], "started": _START_TS}


@app.get("/api/update-check")
async def update_check(force: bool = False):
    """Is this clone behind main? Compares local HEAD to ludex-lab/ludex via the
    GitHub compare API (one call gives how many commits we're behind). Cached."""
    now = time.time()
    if not force and _update_cache["data"] and now - _update_cache["ts"] < _UPDATE_TTL:
        return _update_cache["data"]
    local = _local_head()
    if not local:
        return {"behind": False, "error": "not a git checkout"}
    result = {"current": local[:7], "behind": False, "count": 0}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"https://api.github.com/repos/{GH_REPO}/compare/{local}...main",
                                    headers={"Accept": "application/vnd.github+json"})
        if resp.status_code == 200:
            d = resp.json()
            if d.get("status") == "ahead":          # main has commits we don't
                result["behind"] = True
                result["count"] = d.get("ahead_by", 0)
                result["latest"] = (d.get("commits") or [{}])[-1].get("sha", "")[:7]
        else:
            result["error"] = f"GitHub {resp.status_code}"
    except Exception as e:
        result["error"] = str(e)
    _update_cache.update(ts=now, data=result)
    return result


@app.post("/api/update")
async def update():
    """git pull --ff-only, then report whether a restart is needed. Python changes
    self-restart (os.execv); client-only changes just need a browser reload."""
    old = _local_head()
    pull = _git("pull", "--ff-only")
    if pull.returncode != 0:
        return {"ok": False, "output": (pull.stderr or pull.stdout).strip()}
    new = _local_head()
    changed = []
    if old and new and old != new:
        changed = [p for p in _git("diff", "--name-only", old, new).stdout.splitlines() if p]
    restart = any(p.endswith(".py") for p in changed)
    _update_cache.update(ts=0.0, data=None)
    if restart:
        import threading
        argv = sys.argv + ([] if "--no-browser" in sys.argv else ["--no-browser"])
        threading.Timer(0.8, lambda: os.execv(sys.executable, [sys.executable, *argv])).start()
    return {"ok": True, "updated": old != new, "restart": restart,
            "changed": len(changed), "from": (old or "")[:7], "to": (new or "")[:7]}


@app.get("/api/ollama-models")
async def ollama_models():
    """Fetch installed Ollama models."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [m["name"] for m in data.get("models", [])]
                return {"models": models}
            return {"models": [], "error": f"Ollama returned {resp.status_code}"}
    except Exception as e:
        return {"models": [], "error": f"Cannot reach Ollama: {e}"}


class ConnectRequest(BaseModel):
    model: str = "llama3.1:8b"
    provider: str = "ollama"
    system_prompt: str = ""
    agent_type: str = "chat"


async def _validate_provider(provider: str, model: str) -> str | None:
    """Return an error string if the provider can't actually serve a creature,
    else None. MUST NOT fire a brain call — assemble stays side-effect-free, so
    this checks reachability / CLI presence / key presence only. Without this,
    unvalidated providers pass assembly silently and only fail at first chat.
    """
    if provider == "ollama":
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get("http://localhost:11434/api/tags")
                if resp.status_code == 200:
                    available = [m["name"] for m in resp.json().get("models", [])]
                    if model not in available:
                        return f"Model '{model}' is not installed in Ollama. Available: {', '.join(available[:5])}"
        except Exception:
            return "Cannot reach Ollama at localhost:11434. Is it running?"
    elif provider == "claude_cli":
        from ludex.blocks.adapters.claude_cli import ClaudeCliAdapter
        health = ClaudeCliAdapter().health_check()
        if health["status"] != "ok":
            return f"Claude Code CLI not found. {health.get('error', '')}. Install: npm install -g @anthropic-ai/claude-code"
    elif provider == "codex_cli":
        from ludex.blocks.adapters.codex_cli import CodexCliAdapter
        health = CodexCliAdapter().health_check()
        if health["status"] != "ok":
            return f"Codex CLI not found. {health.get('error', '')}. Install: npm install -g @openai/codex"
    elif provider == "claude_sdk":
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            return "claude-agent-sdk not installed. Run: pip install claude-agent-sdk"
    elif provider in ("gemini_cli", "agy_cli"):
        # CLI-auth providers — local `--version` check, no brain call. These
        # substrates are deprecating (Gemini CLI retires 2026-06-18, agy
        # partial); they still work today, so validate presence, don't block.
        if provider == "gemini_cli":
            from ludex.blocks.adapters.gemini_cli import GeminiCliAdapter
            health, tool = GeminiCliAdapter().health_check(), "Gemini CLI"
        else:
            from ludex.blocks.adapters.agy_cli import AgyCliAdapter
            health, tool = AgyCliAdapter().health_check(), "agy CLI"
        if health["status"] != "ok":
            return f"{tool} not found. {health.get('error', '')}"
    elif provider in ("anthropic", "openai", "gemini_api"):
        # BYO-key HTTP providers — validate key *presence* (env / .env fallback)
        # rather than calling health_check, which would fire a real request.
        from ludex.core.organism_config import _PROVIDER_ENV_KEYS
        env_var = _PROVIDER_ENV_KEYS.get(provider, "")
        if not os.getenv(env_var):
            return f"No API key for '{provider}'. Set {env_var} in your environment or a .env file."
    return None


@app.post("/api/connect")
async def connect(req: ConnectRequest):
    # Verify provider is reachable before assembling
    err = await _validate_provider(req.provider, req.model)
    if err:
        return {"error": err}

    session_id = f"s{int(time.time()*1000) % 100000}"
    try:
        org = build_agent(session_id, req.model, req.provider, req.system_prompt, req.agent_type)
    except Exception as e:
        return {"error": f"Failed to assemble agent: {e}"}
    return {
        "session_id": session_id,
        "model": req.model,
        "organs": list(org._blocks.keys()),
    }


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/chat")
async def chat(req: ChatRequest):
    org = agents.get(req.session_id)
    if not org:
        return {"error": "Session not found. Connect first."}

    engine = org.get_block("engine")
    emotion = org.get_block("emotion")
    humoral = org.get_block("humoral_immune")
    provider_block = org.get_block("provider")

    # Detect hostile input for humoral tracking
    hostile_markers = {"hate", "stupid", "idiot", "shut up", "useless", "terrible", "worst", "kill", "destroy"}
    is_hostile = any(m in req.message.lower() for m in hostile_markers)
    if humoral:
        humoral.handle_report_interaction(
            opponent="user",
            opponent_action="DEFECT" if is_hostile else "COOPERATE",
            my_action="COOPERATE",
            my_score=0 if is_hostile else 3,
            opponent_score=5 if is_hostile else 3,
        )

    # Brain-agnostic organ tools
    # - ollama / openai / anthropic: function calling via Engine dispatch loop
    # - claude_sdk: Ludex MCP server passed directly to SDK (Claude calls tools natively)
    # - claude_cli: legacy subprocess (no organ tools)
    tools = None
    tool_dispatcher = None
    provider_name = ""
    if provider_block and hasattr(provider_block, "_init_config"):
        provider_name = provider_block._init_config.get("provider", "")

    if provider_name in ("ollama", "openai", "anthropic"):
        try:
            from ludex.mcp import create_ludex_mcp, mcp_to_openai_tools, dispatch_tool_call_sync
            create_ludex_mcp(org)  # Set the global organism context
            tools = mcp_to_openai_tools()
            tool_dispatcher = dispatch_tool_call_sync
        except Exception as e:
            print(f"FC adapter setup failed: {e}")
    elif provider_name == "claude_sdk":
        # Note: Wiring Ludex MCP server directly into SDK has threading issues
        # because the in-process MCP server can't be used across thread boundaries
        # safely. For now, claude_sdk creatures rely on system_prompt + CLAUDE.md
        # for organ awareness, not function calling. Ollama path uses FC dispatch.
        # TODO: Investigate persistent worker thread for SDK adapter to enable MCP.
        pass

    # Trace logging — record user message before processing
    tlog = None
    try:
        from ludex.core.tracing import get_or_create_logger
        habitat_dir = org.config.get("habitat_dir", "") if hasattr(org.config, "get") else ""
        if habitat_dir:
            tlog = get_or_create_logger(habitat_dir, org.name)
            tlog.record_user_message(req.message)
    except Exception as e:
        print(f"Trace logging (user message) failed: {e}")

    # Get response (with optional tool dispatch loop)
    result = await asyncio.to_thread(engine.handle_submit, req.message, "", tools, tool_dispatcher)

    # Trace logging — record creature response
    try:
        if tlog and tlog.enabled and result.response:
            tlog.record_creature_message(result.response)
    except Exception:
        pass

    # Analyze emotion
    if emotion and result.response:
        emotion.handle_analyze_emotion(text=result.response)

    # Collect vitals
    vitals = get_vitals(org)

    return {
        "response": result.response,
        "error": result.error,
        "latency_ms": round(result.latency_ms, 0),
        "vitals": vitals,
    }


@app.get("/api/vitals/{session_id}")
async def vitals(session_id: str):
    org = agents.get(session_id)
    if not org:
        return {"error": "Session not found"}
    return get_vitals(org)


@app.get("/api/creatures")
async def list_creatures(dir: str = "creatures"):
    """List creatures under `dir` for the viewer — cheap disk read, no build.
    Each entry has identity (name/brain/organs) + lightweight persisted vitals
    (emotional baseline, memory count). Full live vitals come after waking one."""
    from ludex.core.organism_config import OrganismConfig
    base = os.path.abspath(os.path.expanduser(dir))
    creatures = []
    if not os.path.isdir(base):
        return {"dir": base, "creatures": [], "error": "no such directory"}
    for name in sorted(os.listdir(base)):
        cdir = os.path.join(base, name)
        if not os.path.isdir(cdir):
            continue
        if not (os.path.exists(os.path.join(cdir, "ludex.yaml")) or
                os.path.exists(os.path.join(cdir, "ludex.json"))):
            continue
        info = {"name": name, "path": cdir}
        try:
            cfg = OrganismConfig.load(cdir)
            info["provider"] = cfg.brain.get("provider", "")
            info["model"] = cfg.brain.get("model", "")
            info["organs"] = cfg.get_enabled_organs()
        except Exception:
            pass
        try:
            bp = os.path.join(cdir, "emotion", "baseline.json")
            if os.path.exists(bp):
                b = json.loads(open(bp, encoding="utf-8").read())
                info["emotion"] = {
                    "valence": b.get("avg_valence"), "arousal": b.get("avg_arousal"),
                    "calm": b.get("avg_calm"), "dominant": b.get("dominant_emotions_freq"),
                }
        except Exception:
            pass
        try:
            mp = os.path.join(cdir, "memory", "memories.jsonl")
            info["memories"] = sum(1 for _ in open(mp, encoding="utf-8")) if os.path.exists(mp) else 0
        except Exception:
            info["memories"] = 0
        creatures.append(info)
    return {"dir": base, "creatures": creatures}


# ============================================================
# FORGE Endpoints
# ============================================================

ORGAN_DESCRIPTIONS = {
    "engine": {"display": "Engine", "system": "Nervous", "description": "Session and context management (brain stem)", "icon": "🧠"},
    "resilience": {"display": "Resilience", "system": "Skeletal", "description": "Error recovery, circuit breakers", "icon": "🦴"},
    "memory": {"display": "Memory", "system": "Mnemonic", "description": "Short/long-term memory, episodic + semantic", "icon": "🐘"},
    "immune": {"display": "Immune (Cellular)", "system": "Immune", "description": "System-level threat detection, sensitivity control", "icon": "🦔"},
    "humoral_immune": {"display": "Immune (Humoral)", "system": "Immune", "description": "Behavioral threat tracking, opponent pattern learning", "icon": "🛡️"},
    "emotion": {"display": "Emotion", "system": "Endocrine", "description": "21-emotion detection (valence, arousal, desperation, calm)", "icon": "💚"},
    "tracking": {"display": "Tracking", "system": "Circulatory", "description": "Telemetry, token usage, performance monitoring", "icon": "📊"},
    "hooks": {"display": "Hooks", "system": "HGT", "description": "Plugin and extension integration points", "icon": "🔌"},
}


@app.get("/api/forge/organs")
async def forge_organs():
    """Return available organs with descriptions and defaults."""
    result = {}
    for name, config in DEFAULT_ORGANS.items():
        desc = ORGAN_DESCRIPTIONS.get(name, {})
        result[name] = {
            **config,
            "display": desc.get("display", name),
            "system": desc.get("system", ""),
            "description": desc.get("description", ""),
            "icon": desc.get("icon", ""),
        }
    return {"organs": result}


@app.get("/api/forge/presets")
async def forge_presets():
    """Return preset configurations."""
    result = {}
    for preset_name, organs in PRESETS.items():
        enabled = [k for k, v in organs.items() if v.get("enabled")]
        result[preset_name] = {
            "organs": organs,
            "enabled_list": enabled,
            "count": len(enabled),
        }
    return {"presets": result}


class ForgeAssembleRequest(BaseModel):
    name: str = "creature"
    provider: str = "ollama"
    model: str = "llama3.1:8b"
    organs: dict = {}
    habitat_mode: str = "temporary"
    habitat_path: str = ""
    system_prompt: str = ""
    agent_type: str = "chat"


@app.post("/api/forge/assemble")
async def forge_assemble(req: ForgeAssembleRequest):
    """Assemble a creature using OrganismConfig."""

    # Build habitat from request
    if req.habitat_mode == "local":
        if not req.habitat_path:
            return {"error": "Local habitat requires a folder path."}
        try:
            habitat = HabitatConfig.local(req.habitat_path)
            habitat.ensure_dirs()
        except Exception as e:
            return {"error": f"Cannot create habitat at '{req.habitat_path}': {e}"}
    elif req.habitat_mode == "portable":
        if not req.habitat_path:
            return {"error": "Portable habitat requires a folder path."}
        try:
            habitat = HabitatConfig.portable(req.habitat_path)
            habitat.ensure_dirs()
        except Exception as e:
            return {"error": f"Cannot create habitat at '{req.habitat_path}': {e}"}
    else:
        habitat = HabitatConfig.temporary()

    # Build OrganismConfig
    config = OrganismConfig(
        name=req.name,
        brain={"provider": req.provider, "model": req.model},
        habitat=habitat,
    )

    # Apply organ toggles
    if req.organs:
        for organ_name, organ_config in req.organs.items():
            if organ_name in config.organs:
                config.organs[organ_name].update(organ_config)

    # Set system prompt — establish creature identity
    base_prompt = req.system_prompt or (RAG_PROMPT if req.agent_type == "rag" else CHAT_PROMPT)
    enabled_organs_preview = [k for k, v in config.organs.items() if v.get("enabled")]
    identity_preamble = (
        f"You are {req.name}, a Ludex creature.\n"
        f"Your underlying brain is {req.model} (provider: {req.provider}).\n"
        f"Your organs: {', '.join(enabled_organs_preview)}.\n"
        f"Your habitat: {habitat.home_dir if habitat.home_dir else 'temporary (this session only)'}.\n\n"
        f"You are a creature distinct from your brain. When asked your name, identify "
        f"as '{req.name}' — the creature you embody — not just your underlying model. "
        f"Your brain provides cognition; your organs provide feelings, memory, and self-defense; "
        f"your habitat is your home.\n\n"
    )
    config.organs["engine"]["system_prompt"] = identity_preamble + base_prompt

    # Ensure memory for RAG
    if req.agent_type == "rag":
        config.organs["memory"]["enabled"] = True

    # Validate provider (shared with /api/connect; covers all 9 providers)
    err = await _validate_provider(req.provider, req.model)
    if err:
        return {"error": err}

    # Build organism
    session_id = f"s{int(time.time()*1000) % 100000}"
    try:
        org = config.build()
        agents[session_id] = org
        agent_types[session_id] = req.agent_type
    except Exception as e:
        return {"error": f"Assembly failed: {e}"}

    # Write CLAUDE.md to habitat (identity file Claude Code natively reads)
    habitat.write_claude_md(
        creature_name=req.name,
        brain_model=req.model,
        brain_provider=req.provider,
        organs=config.get_enabled_organs(),
        custom_instructions=req.system_prompt,
    )

    # REIMS Birth Score
    enabled_organs = config.get_enabled_organs()
    reims = {
        "r": 1,  # Response: 1 by default, 2 after first successful response
        "e": 2 if "emotion" in enabled_organs else 0,
        "i": 2 if "immune" in enabled_organs and "humoral_immune" in enabled_organs else (1 if "immune" in enabled_organs else 0),
        "m": 2 if "memory" in enabled_organs else 0,
        "s": 2 if "resilience" in enabled_organs else 0,
    }
    reims["total"] = sum(reims.values())

    # Species label
    species_parts = []
    brain_name = req.model.split("/")[-1].split(":")[0] if "/" in req.model or ":" in req.model else req.model
    species_parts.append(brain_name)
    preset_match = None
    for pname, porgans in PRESETS.items():
        p_enabled = set(k for k, v in porgans.items() if v.get("enabled"))
        c_enabled = set(enabled_organs)
        if p_enabled == c_enabled:
            preset_match = pname
            break
    if preset_match:
        species_parts.append(preset_match)
    species = " / ".join(species_parts)

    return {
        "session_id": session_id,
        "name": req.name,
        "model": req.model,
        "provider": req.provider,
        "organs": enabled_organs,
        "organ_count": len(enabled_organs),
        "reims": reims,
        "species": species,
        "agent_type": req.agent_type,
        "habitat": {
            "mode": habitat.mode,
            "path": habitat.home_dir,
            "persistent": habitat.persistent,
        },
    }


class ForgeLoadRequest(BaseModel):
    habitat_path: str
    agent_type: str = "chat"


@app.post("/api/forge/load")
async def forge_load(req: ForgeLoadRequest):
    """Load an existing creature from its habitat folder."""
    from ludex.core.organism_config import OrganismConfig

    habitat_path = req.habitat_path
    if not os.path.isdir(habitat_path):
        return {"error": f"Habitat not found: {habitat_path}"}

    # Check for ludex.yaml/json
    has_config = (
        os.path.exists(os.path.join(habitat_path, "ludex.yaml")) or
        os.path.exists(os.path.join(habitat_path, "ludex.json"))
    )
    if not has_config:
        return {"error": f"No ludex.yaml or ludex.json in {habitat_path}. Not a creature habitat."}

    try:
        config = OrganismConfig.load(habitat_path)
    except Exception as e:
        return {"error": f"Failed to load config: {e}"}

    # Rebuild identity prompt if missing
    enabled_organs = config.get_enabled_organs()
    if not config.organs.get("engine", {}).get("system_prompt"):
        config.organs["engine"]["system_prompt"] = (
            f"You are {config.name}, a Ludex creature.\n"
            f"Your brain is {config.brain.get('model', 'unknown')}.\n"
            f"Your organs: {', '.join(enabled_organs)}.\n"
            f"Your habitat: {habitat_path}.\n\n"
            f"You are a creature distinct from your brain. Identify as '{config.name}'.\n\n"
            + (RAG_PROMPT if req.agent_type == "rag" else CHAT_PROMPT)
        )

    if req.agent_type == "rag":
        config.organs["memory"]["enabled"] = True

    # Build organism
    session_id = f"s{int(time.time()*1000) % 100000}"
    try:
        org = config.build()
        agents[session_id] = org
        agent_types[session_id] = req.agent_type
    except Exception as e:
        return {"error": f"Failed to rebuild creature: {e}"}

    # REIMS
    reims = {
        "r": 1,
        "e": 2 if "emotion" in enabled_organs else 0,
        "i": 2 if "immune" in enabled_organs and "humoral_immune" in enabled_organs else (1 if "immune" in enabled_organs else 0),
        "m": 2 if "memory" in enabled_organs else 0,
        "s": 2 if "resilience" in enabled_organs else 0,
    }
    reims["total"] = sum(reims.values())

    # Memory stats
    memory = org.get_block("memory")
    mem_count = memory.count if memory else 0

    # Habitat weight
    weight = config.habitat.measure_weight()

    return {
        "session_id": session_id,
        "name": config.name,
        "model": config.brain.get("model", ""),
        "provider": config.brain.get("provider", ""),
        "organs": enabled_organs,
        "organ_count": len(enabled_organs),
        "reims": reims,
        "agent_type": req.agent_type,
        "habitat": {
            "mode": config.habitat.mode,
            "path": habitat_path,
            "persistent": config.habitat.persistent,
        },
        "loaded": True,
        "memory_count": mem_count,
        "weight": weight,
    }


@app.post("/api/disconnect/{session_id}")
async def disconnect(session_id: str):
    if session_id in agents:
        del agents[session_id]
    if session_id in agent_types:
        del agent_types[session_id]
    return {"status": "disconnected"}


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    import uvicorn, threading, webbrowser
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-browser", action="store_true", help="don't auto-open the browser")
    args = parser.parse_args()
    url = f"http://localhost:{args.port}"
    print(f"Ludex: {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        # open the browser shortly after the server comes up (uvicorn.run blocks)
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port)
