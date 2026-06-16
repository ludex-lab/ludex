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
from ludex.core.memory_types import IMPORTANCE_SESSION_CHAT


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


def _enable_web_multiturn(org):
    """Give a web creature conversational continuity: tell its provider adapter to
    send the full message history each turn. claude_cli otherwise sends only the
    last user message (so the creature re-greets every turn); other adapters
    already use history, so this is a harmless no-op for them. Web-only — the
    research corpus / CLI paths never call this, so their behavior is unchanged."""
    try:
        pb = org.get_block("provider")
        if pb is not None and getattr(pb, "_adapter", None) is not None:
            setattr(pb._adapter, "_full_history", True)
    except Exception as e:
        print(f"web multiturn enable failed: {e}")


def _session_transcript(engine, max_turns: int = 12) -> str:
    """Compact transcript of the current session from the engine's message log —
    ground-truth material handed to the sleep reflection so SELF.md is shaped by
    what actually happened this session (not just fuzzy memory recall)."""
    msgs = [m for m in getattr(engine, "_messages", []) if getattr(m, "role", "") in ("user", "assistant")]
    if not msgs:
        return ""
    lines = []
    for m in msgs[-max_turns * 2:]:
        who = "User" if m.role == "user" else "You"
        lines.append(f"{who}: {m.content}")
    return "This session's conversation just now:\n" + "\n".join(lines)


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
    _enable_web_multiturn(org)
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
    # no-cache: the app updates via git pull + restart, so the browser must
    # revalidate index.html each load — otherwise a cached old client shows stale
    # state after an update (looked like sessions were "stuck"; they weren't).
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"),
                        headers={"Cache-Control": "no-cache"})


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


@app.get("/api/health")
async def health():
    """Liveness ping for the web header's connection light. Cheap, no side
    effects — the frontend polls this on a heartbeat so the user sees red the
    moment the local server goes down."""
    return {"ok": True}


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

    # D-024 / F1 (2026-06-12): per-TURN chat capture removed. A turn is
    # telemetry granularity (brain_call spans already record it); the
    # conversation as lived experience is captured ONCE per session at
    # disconnect/sleep (see /api/disconnect), and its meaning lands via
    # the sleep reflection. Per-turn captures were inflating memory and
    # shadowing real reflections in recall (2026-06-11 finding).

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


def _scan_creatures(base: str) -> list:
    """Cheap disk scan of creatures under `base` — identity + lightweight persisted
    vitals (emotional baseline, memory count) + bond targets. No organism build."""
    from ludex.core.organism_config import OrganismConfig
    creatures = []
    if not os.path.isdir(base):
        return creatures
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
        try:
            bdir = os.path.join(cdir, "bonds")
            info["bonds"] = sorted(os.path.splitext(f)[0] for f in os.listdir(bdir)
                                   if f.endswith(".md")) if os.path.isdir(bdir) else []
        except Exception:
            info["bonds"] = []
        creatures.append(info)
    return creatures


@app.get("/api/creatures")
async def list_creatures(dir: str = "creatures"):
    """List creatures under `dir` for the viewer — cheap disk read, no build."""
    base = os.path.abspath(os.path.expanduser(dir))
    if not os.path.isdir(base):
        return {"dir": base, "creatures": [], "error": "no such directory"}
    return {"dir": base, "creatures": _scan_creatures(base)}


@app.get("/api/ecosystem")
async def ecosystem(dir: str = "creatures"):
    """Ecosystem overview: every creature + the bond graph between them. Bonds are
    directional (a creature has a bonds/<other>.md for each peer it models); a pair
    modeled both ways is 'mutual'."""
    base = os.path.abspath(os.path.expanduser(dir))
    creatures = _scan_creatures(base)
    by_lower = {c["name"].lower(): c["name"] for c in creatures}
    directed = set()
    for c in creatures:
        for b in c.get("bonds", []):
            to = by_lower.get(str(b).lower())
            if to and to != c["name"]:
                directed.add((c["name"], to))
    edges, emitted = [], set()
    for (a, b) in sorted(directed):
        if (b, a) in directed:               # mutual → one undirected edge
            key = tuple(sorted((a, b)))
            if key in emitted:
                continue
            emitted.add(key)
            edges.append({"from": a, "to": b, "mutual": True})
        else:                                 # one-way → directed edge
            edges.append({"from": a, "to": b, "mutual": False})
    stats = {
        "creatures": len(creatures),
        "edges": len(edges),
        "mutual": sum(1 for e in edges if e["mutual"]),
        "brains": sorted({c.get("provider", "") for c in creatures if c.get("provider")}),
    }
    return {"dir": base, "creatures": creatures, "edges": edges, "stats": stats}


# ============================================================
# Timeline — longitudinal observatory (M4): snapshots, retrospectives,
# milestone spans, journal — generic over any creature's habitat data.
# Cheap disk reads only; no organism build.
# ============================================================

# Span kinds that count as life milestones on the timeline. Routine kinds
# (tick, reward, brain_call, ...) stay out — the timeline shows the shape
# of a life, not the telemetry.
_TIMELINE_SPAN_KINDS = {
    "substrate_transition", "identity_promotion", "consolidation",
    "model_currency", "brain_substrate_upgrade",
}


def _safe_creature_dir(name: str, dir: str = "creatures") -> str | None:
    """Resolve a creature habitat dir, rejecting path traversal."""
    if not name or any(s in name for s in ("/", "\\", "..")):
        return None
    base = os.path.abspath(os.path.expanduser(dir))
    cdir = os.path.join(base, name)
    return cdir if os.path.isdir(cdir) else None


def _read_frontmatter(path: str, max_bytes: int = 800) -> dict:
    """Parse the simple `--- key: value ---` frontmatter block of a .md file."""
    out: dict = {}
    try:
        head = open(path, encoding="utf-8").read(max_bytes)
    except OSError:
        return out
    if not head.startswith("---"):
        return out
    for line in head[3:].split("---", 1)[0].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


@app.get("/api/creature/{name}/timeline")
async def creature_timeline(name: str, dir: str = "creatures"):
    """Chronological life events for one creature (newest first)."""
    cdir = _safe_creature_dir(name, dir)
    if cdir is None:
        return {"error": "no such creature"}
    events = []

    sdir = os.path.join(cdir, "snapshots")
    if os.path.isdir(sdir):
        for snap in sorted(os.listdir(sdir)):
            meta_p = os.path.join(sdir, snap, "snapshot.json")
            if not os.path.isfile(meta_p):
                continue
            try:
                meta = json.loads(open(meta_p, encoding="utf-8").read())
            except Exception:
                continue
            events.append({
                "ts": meta.get("timestamp", 0), "type": "snapshot",
                "title": meta.get("reason", snap), "detail": meta.get("note", ""),
                "ref": snap,
            })

    rdir = os.path.join(cdir, "reflections")
    if os.path.isdir(rdir):
        for f in sorted(os.listdir(rdir)):
            if not f.endswith(".md"):
                continue
            fm = _read_frontmatter(os.path.join(rdir, f))
            try:
                ts = float(fm.get("consolidated_on_ts", 0))
            except ValueError:
                ts = 0
            events.append({
                "ts": ts, "type": "retrospective",
                "title": f"{fm.get('window_from', '?')} → {fm.get('window_to', '?')}",
                "detail": f"{fm.get('events', '?')} events · {fm.get('synthesizer', '')}",
                "ref": os.path.splitext(f)[0],
            })

    spans_p = os.path.join(cdir, "store", "spans.jsonl")
    if os.path.isfile(spans_p):
        try:
            for line in open(spans_p, encoding="utf-8"):
                try:
                    s = json.loads(line)
                except Exception:
                    continue
                if s.get("kind") not in _TIMELINE_SPAN_KINDS:
                    continue
                a = s.get("attributes", {})
                if s["kind"] == "substrate_transition":
                    title = f"[{a.get('axis', '?')}] {a.get('from', '?')} → {a.get('to', '?')}"
                    detail = f"{a.get('op', '')} ({a.get('magnitude', '')})"
                elif s["kind"] == "identity_promotion":
                    title = f"identity: +{len(a.get('promoted', []))} settled, +{len(a.get('nominated', []))} provisional"
                    detail = "; ".join(a.get("promoted", [])[:2])
                else:
                    title = a.get("model") or a.get("reflection_file") or s["kind"]
                    detail = a.get("status", "") or a.get("note", "") or ""
                events.append({"ts": s.get("timestamp", 0), "type": s["kind"],
                               "title": str(title), "detail": str(detail)[:200],
                               "ref": ""})
        except OSError:
            pass

    jdir = os.path.join(cdir, "journal")
    if os.path.isdir(jdir):
        for f in sorted(os.listdir(jdir)):
            if not f.endswith(".md"):
                continue
            p = os.path.join(jdir, f)
            events.append({"ts": os.path.getmtime(p), "type": "journal",
                           "title": os.path.splitext(f)[0], "detail": "", "ref": f})

    events.sort(key=lambda e: e["ts"], reverse=True)
    for e in events:
        e["date"] = time.strftime("%Y-%m-%d", time.localtime(e["ts"])) if e["ts"] else "?"
    return {"name": name, "events": events}


def _read_bond(path):
    """Parse a bonds/<peer>.md — a '# Bond: x / First met: .. / Brain: ..' header, then
    the narrative body after the first blank line."""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return {"first_met": "", "text": ""}
    first_met, body_at = "", 0
    for i, ln in enumerate(lines):
        if ln.lower().startswith("first met:"):
            first_met = ln.split(":", 1)[1].strip()
        if i > 0 and not ln.strip():
            body_at = i + 1
            break
    return {"first_met": first_met, "text": "\n".join(lines[body_at:]).strip()}


def _build_creature_profile(name, dir, events):
    """Assemble a creature's shareable PROFILE SNAPSHOT (ludex.profile/v1) — the structured
    'digital twin' data a viewer renders (2D now, a 3D avatar/habitat/virtual-city later;
    3D is a renderer over THIS data, not a rewrite). Only distilled, shareable narrative —
    identity, SELF, timeline, bonds, emotional state — never raw spans (D-044)."""
    cdir = _safe_creature_dir(name, dir)
    if cdir is None:
        return None
    base = os.path.abspath(os.path.expanduser(dir))
    info = next((c for c in _scan_creatures(base) if c["name"] == name), {"name": name})
    self_md = ""
    sp = os.path.join(cdir, "SELF.md")
    if os.path.isfile(sp):
        self_md = open(sp, encoding="utf-8").read()
    # cross-machine re-recognition index (B1): peer name -> {creature_id, encounters}
    lxm_idx = {}
    lp = os.path.join(cdir, "lxm_bonds.json")
    if os.path.isfile(lp):
        try:
            for cid, e in json.loads(open(lp, encoding="utf-8").read()).items():
                lxm_idx[str(e.get("name", "")).lower()] = {"creature_id": cid, "encounters": e.get("encounters")}
        except Exception:
            pass
    bonds = []
    bdir = os.path.join(cdir, "bonds")
    if os.path.isdir(bdir):
        for f in sorted(os.listdir(bdir)):
            if not f.endswith(".md"):
                continue
            peer = os.path.splitext(f)[0]
            b = {"name": peer, **_read_bond(os.path.join(bdir, f))}
            x = lxm_idx.get(peer.lower())
            if x:
                b["creature_id"] = x["creature_id"]; b["encounters"] = x["encounters"]; b["cross_machine"] = True
            bonds.append(b)
    brain = ":".join(p for p in (info.get("provider", ""), info.get("model", "")) if p)
    return {
        "format": "ludex.profile/v1",
        "creature": {"name": info.get("name", name), "brain": brain,
                     "organs": info.get("organs", []), "memories": info.get("memories", 0)},
        "state": {"emotion": info.get("emotion")},     # the 3D-visualizable slot
        "self": self_md,
        "timeline": events,
        "bonds": bonds,
        "habitat": {},          # extensible: P3 ecosystem / a future 3D habitat render
        "published_at": None,   # stamped at publish (P2)
    }


@app.get("/api/creature/{name}/profile")
async def creature_profile(name: str, dir: str = "creatures"):
    """The shareable profile snapshot (ludex.profile/v1) — the structured 'digital twin' a
    viewer renders. P0: built + previewed locally to lock the format before any hosting
    (P1 static viewer) or publish service (P2)."""
    events = (await creature_timeline(name, dir)).get("events", [])
    prof = _build_creature_profile(name, dir, events)
    return prof if prof is not None else {"error": "no such creature"}


@app.get("/api/creature/{name}/self-history")
async def creature_self_history(name: str, dir: str = "creatures"):
    """SELF.md version metadata: every snapshot that froze a SELF.md, plus
    current. Content fetched per-version via /self/{version} (some creatures
    carry 100+ snapshots — shipping all texts at once would be megabytes)."""
    cdir = _safe_creature_dir(name, dir)
    if cdir is None:
        return {"error": "no such creature"}
    versions = []
    sdir = os.path.join(cdir, "snapshots")
    if os.path.isdir(sdir):
        for snap in sorted(os.listdir(sdir)):
            self_p = os.path.join(sdir, snap, "SELF.md")
            meta_p = os.path.join(sdir, snap, "snapshot.json")
            if not os.path.isfile(self_p):
                continue
            ts = 0
            try:
                ts = json.loads(open(meta_p, encoding="utf-8").read()).get("timestamp", 0)
            except Exception:
                ts = os.path.getmtime(self_p)
            versions.append({"id": snap, "ts": ts,
                             "date": time.strftime("%Y-%m-%d", time.localtime(ts)),
                             "chars": os.path.getsize(self_p)})
    versions.sort(key=lambda v: v["ts"])
    if os.path.isfile(os.path.join(cdir, "SELF.md")):
        versions.append({"id": "current", "ts": time.time(),
                         "date": "now",
                         "chars": os.path.getsize(os.path.join(cdir, "SELF.md"))})
    return {"name": name, "versions": versions}


@app.get("/api/creature/{name}/self/{version}")
async def creature_self_version(name: str, version: str, dir: str = "creatures"):
    """One SELF.md version's text — `current` or a snapshot dir name."""
    cdir = _safe_creature_dir(name, dir)
    if cdir is None:
        return {"error": "no such creature"}
    if any(s in version for s in ("/", "\\", "..")):
        return {"error": "bad version"}
    p = (os.path.join(cdir, "SELF.md") if version == "current"
         else os.path.join(cdir, "snapshots", version, "SELF.md"))
    if not os.path.isfile(p):
        return {"error": "no such version"}
    return {"name": name, "version": version,
            "text": open(p, encoding="utf-8").read()}


@app.get("/api/creature/{name}/reflection/{stem}")
async def creature_reflection(name: str, stem: str, dir: str = "creatures"):
    """Full text of one consolidation retrospective."""
    cdir = _safe_creature_dir(name, dir)
    if cdir is None:
        return {"error": "no such creature"}
    if any(s in stem for s in ("/", "\\", "..")):
        return {"error": "bad stem"}
    p = os.path.join(cdir, "reflections", f"{stem}.md")
    if not os.path.isfile(p):
        return {"error": "no such reflection"}
    return {"name": name, "stem": stem, "text": open(p, encoding="utf-8").read()}


# ============================================================
# Field sessions — admit creatures into a field, run it, observe (background)
# ============================================================
field_sessions = {}  # sid -> {field, status, error, field_kind}  (in-memory, live)
FIELD_LOG = os.path.join(REPO_ROOT, "field_log")  # finished sessions persisted here (survive restarts)

# Declarative field taxonomy — the single source of truth the Field tab renders.
# Internal fields run via _run_field_bg. Add a field = add an entry.
# Served at GET /api/fields.
FIELD_REGISTRY = {
    "internal": [
        {"id": "council", "name": "Council", "i18n": "fld_council", "viewer": "transcript",
         "min_creatures": 2, "prompt": "dilemma", "mediator": True, "impl": True},
        {"id": "forum", "name": "Forum", "i18n": "fld_forum", "viewer": "transcript",
         "min_creatures": 2, "prompt": "claim", "impl": True},
        {"id": "wilderness", "name": "Wilderness", "i18n": "fld_wild", "viewer": "ticklog",
         "min_creatures": 1, "prompt": None, "ticks": True, "impl": True},
    ],
    "external": [
        {"id": "lxm", "name": "Ludus ex Machina", "impl": True, "viewer": "lxm",
         "note": "A cross-machine match on the hosted LxM arena — your creature plays a real game, you watch the replay, it reflects.",
         "games": [{"id": "trustgame", "name": "Trust Game"},
                   {"id": "tictactoe", "name": "Tic-Tac-Toe"},
                   {"id": "chess", "name": "Chess"},
                   {"id": "poker", "name": "Poker (heads-up)"}]},
    ],
}


def _session_transcript_records(field):
    out = []
    if field is None:
        return out
    if hasattr(field, "transcript_records"):      # LxM hosted match (LxMField)
        return list(field.transcript_records)
    if hasattr(field, "rounds"):                  # council / forum
        for rd in field.rounds:
            for rec in rd.records:
                out.append({"round": rec.round_index, "phase": rec.phase,
                            "participant": rec.participant, "kind": rec.kind, "content": rec.content,
                            "attributes": getattr(rec, "attributes", None) or None})
    elif hasattr(field, "log"):                   # wilderness — tick log
        for t in field.log:
            out.append({"round": t.get("tick"), "phase": "event", "participant": "world",
                        "kind": "event",
                        "content": f"{t.get('event', '')} — {t.get('event_description', '')}",
                        "attributes": {"category": t.get("event_category")}})
            for c in t.get("creatures", []):
                out.append({"round": t.get("tick"), "phase": "tick",
                            "participant": c.get("name"), "kind": "action",
                            "content": c.get("response", ""),
                            "attributes": {"action": c.get("action"), "energy": c.get("energy"),
                                           "emotion": c.get("emotion", ""),
                                           "threat": c.get("threat")}})
    return out


def _field_participant_names(field) -> list:
    if field is None:
        return []
    if hasattr(field, "participant_names"):        # LxM hosted match (LxMField)
        return list(field.participant_names)
    if hasattr(field, "participants"):
        return [p.name for p in field.participants]
    if hasattr(field, "creatures"):               # wilderness
        return [c.name for c in field.creatures]
    return []


def _count_turns(records) -> int:
    """History-line activity count: creature contributions only — excludes
    the dilemma/claim posting and wilderness world-event rows. Field-type
    agnostic (reads the mapped transcript, not field.rounds)."""
    return sum(1 for r in records
               if r.get("phase") not in ("dilemma_posed", "claim", "event"))


def _save_field_session(sess):
    """Persist a finished session to disk so it survives server restarts (history)."""
    if sess.get("status") == "error":
        return   # don't keep failed matches — they clutter the list and break the view on click
    if sess.get("field_kind") == "lxm" and sess.get("kind") == "practice":
        return   # practice = ephemeral, nothing kept — only published matches reach the history
    try:
        os.makedirs(FIELD_LOG, exist_ok=True)
        field = sess.get("field")
        data = {
            "sid": sess.get("sid"), "field_kind": sess.get("field_kind"), "status": sess.get("status"),
            "dilemma": sess.get("dilemma", ""), "mediator": sess.get("mediator", ""),
            "game": sess.get("game", ""), "kind": sess.get("kind", ""), "viewer_url": sess.get("viewer_url"),
            "participants": _field_participant_names(field) or sess.get("entered", []),
            "started": sess.get("started", 0), "ended": time.time(),
            "transcript": _session_transcript_records(field),
            "verdict": sess.get("verdict"), "scores": sess.get("scores"), "result": sess.get("result"),
        }
        with open(os.path.join(FIELD_LOG, f"{sess.get('sid')}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"field session save failed: {e}")


def _build_creature_org(habitat_path: str):
    """Load + build a creature organism from its habitat (for field participation)."""
    from ludex.core.organism_config import OrganismConfig
    config = OrganismConfig.load(habitat_path)
    enabled = config.get_enabled_organs()
    if not config.organs.get("engine", {}).get("system_prompt"):
        config.organs["engine"]["system_prompt"] = (
            f"You are {config.name}, a Ludex creature.\n"
            f"Your brain is {config.brain.get('model', 'unknown')}.\n"
            f"Your organs: {', '.join(enabled)}.\n\n"
            f"You are a creature distinct from your brain. Identify as '{config.name}'.\n\n"
            + CHAT_PROMPT
        )
    return config.build()


class LxMField:
    """A hosted cross-machine LxM match's transcript (web UX, Enter→Watch→Reflect):
    the move-by-move record the Field-tab observe view renders + the LxM viewer
    deep-link + the result. The creature plays via an EPHEMERAL copy (D-090 — the
    live creature is untouched; for `published`, a distilled 회고 writeback is a
    later step)."""
    def __init__(self, creature_name, opponent_name="house"):
        self.transcript_records = []
        self.participant_names = [creature_name, opponent_name]
        self.viewer_url = None
        self.result = None

    def add_turn(self, rec):
        move = rec.get("move")
        act = move.get("action") if isinstance(move, dict) else move
        content = str(act if act is not None else move)
        if rec.get("dialogue"):
            content += f" — “{rec['dialogue']}”"
        self.transcript_records.append({
            "round": rec.get("turn"), "phase": "move", "participant": rec.get("who"),
            "kind": "move", "content": content,
            "attributes": {"move": move, "readable": rec.get("readable")}})


def _lxm_friendly_error(exc):
    """Turn a raw match exception into a calm, user-facing message + a kind the UI can act
    on. The common failure is the hosted arena being asleep/unreachable (it idles when no
    one is playing), which is transient — say so and invite a retry, rather than surface an
    HTTP traceback that reads like the user broke something."""
    code = getattr(exc, "code", "") or ""
    if code in ("network", "poll_timeout") or code.startswith("http_5"):
        return ("The Ludus ex Machina arena couldn't be reached just now — it may be waking "
                "up (it sleeps when no one is playing). Give it a moment and try again.",
                "unreachable")
    return ("Something interrupted the match on the arena. Please try again in a moment.", "error")


_LXM_GAME_NAMES = {"trustgame": "Trust Game", "tictactoe": "Tic-Tac-Toe", "chess": "Chess",
                   "poker": "Poker", "avalon": "Avalon", "codenames": "Codenames",
                   "blockworld": "Blockworld", "deduction": "Deduction"}

# The arena's game roster with player ranges — fetched live (GET /api/games),
# cached, with a static fallback so the Field still works on a cold start.
_LXM_FALLBACK_GAMES = [
    {"id": "tictactoe", "min_players": 2, "max_players": 2},
    {"id": "chess", "min_players": 2, "max_players": 2},
    {"id": "trustgame", "min_players": 2, "max_players": 2},
    {"id": "poker", "min_players": 2, "max_players": 6},
    {"id": "codenames", "min_players": 4, "max_players": 4},
    {"id": "avalon", "min_players": 5, "max_players": 10},
    {"id": "deduction", "min_players": 1, "max_players": 1},
    {"id": "blockworld", "min_players": 1, "max_players": 8},
]
_LXM_GAMES_CACHE = {"at": 0.0, "games": None}


def _lxm_games():
    """The LxM arena's playable games + player ranges — [{id, min_players, max_players}].
    Cached 5 min; falls back to the static set when the arena is unreachable (cold start)."""
    import urllib.request
    from ludex.bridges.hosted_match import ONRENDER
    if _LXM_GAMES_CACHE["games"] and time.time() - _LXM_GAMES_CACHE["at"] < 300:
        return _LXM_GAMES_CACHE["games"]
    try:
        with urllib.request.urlopen(ONRENDER + "/api/games", timeout=20) as r:
            games = json.loads(r.read().decode())
        if isinstance(games, list) and games:
            _LXM_GAMES_CACHE.update(at=time.time(), games=games)
            return games
    except Exception:
        pass
    return _LXM_GAMES_CACHE["games"] or _LXM_FALLBACK_GAMES


def _lxm_aftermath(sess, creature_path, game, field):
    """published only — the creature remembers + reflects on the encounter, like an
    internal field (Council/Forum): a reflection (→ SELF.md) written to the LIVE creature.
    Only this DISTILLED trace crosses over; the raw match was played on an ephemeral copy
    (D-090), and nothing raw is written here. Reuses selfhood.reflect — the same call the
    internal-field aftermath makes — so an LxM encounter is remembered the way a Council
    is. A bond is NOT written toward the scripted house bot (see below)."""
    from ludex.core import selfhood
    me = field.participant_names[0]
    opp = field.participant_names[1] if len(field.participant_names) > 1 else "an opponent"
    gname = _LXM_GAME_NAMES.get(game, game)
    summary = field.result.get("summary", "") if isinstance(field.result, dict) else ""
    moves = "\n".join(f"  [{r.get('round')}] {r.get('participant')}: {r.get('content')}"
                      for r in field.transcript_records)
    context = (f"On Ludus ex Machina — a cross-machine arena where you meet minds from other "
               f"habitats — you played a {gname} against {opp}.\n{summary}\n\nMove by move:\n{moves}")
    sess["status"] = "reflecting"
    org = _build_creature_org(creature_path)        # the LIVE creature, writable — intended accumulation, as in Council
    engine = org.get_block("engine")
    sess["aftermath"] = f"reflect:{me}"
    try:
        selfhood.reflect(org, "ludus_ex_machina", engine, context)
    except Exception as e:
        print(f"lxm reflect failed: {e}")
    # No bond toward the scripted house bot — a bond is a verified memory of another
    # MIND (a creature), not a script (JJ, 2026-06-14): the reflection already remembers
    # the encounter. When real creature-vs-creature matches land (B1/Stage B
    # re-recognition), the bond is written here toward the actual creature opponent.
    sess["aftermath"] = ""


def _run_lxm_match_bg(sid: str, game: str, creature_path: str, kind: str):
    """Background worker: a creature plays a cross-machine match on the hosted LxM
    arena (D-089 §5). Runs EPHEMERAL (D-090 — live creature never mutated); streams
    each move into an LxMField; on completion stores the result + viewer deep-link."""
    from ludex.bridges.hosted_match import play_hosted_match, HOUSE_BOTS
    sess = field_sessions[sid]
    try:
        name0 = os.path.basename(str(creature_path).rstrip("/\\"))
        opp = HOUSE_BOTS.get(game)
        if opp is None:
            raise ValueError(f"No house opponent for '{game}'.")
        # A watchable length for the web UX — trustgame is iterated, so cap it
        # short (6 rounds); tic-tac-toe ends naturally in ≤9.
        max_turns = {"trustgame": 12, "tictactoe": 9}.get(game, 16)
        field = LxMField(name0)
        sess["field"] = field
        sess["entered"] = [name0]; sess["building"] = ""
        sess["status"] = "running"; sess["building"] = "the Ludus ex Machina arena"; sess["thinking"] = ""

        def on_turn(rec):
            field.add_turn(rec)
            sess["thinking"] = rec.get("who")

        result = play_hosted_match(creature_path, opp, game=game, kind=kind, max_turns=max_turns,
                                   on_turn=on_turn, on_start=lambda u: sess.update(viewer_url=u, building=""),
                                   should_stop=lambda: sess.get("stop"))
        field.result = result.get("result")
        field.viewer_url = result.get("viewer_url")
        sess["result"] = result.get("result")
        sess["viewer_url"] = result.get("viewer_url")
        sess["scores"] = (result.get("result") or {}).get("scores")
        if sess.get("stop"):
            sess["status"] = "stopped"
        else:
            if kind == "published":      # the encounter counts: remember + reflect on the LIVE creature (S1b)
                _lxm_aftermath(sess, creature_path, game, field)
            sess["status"] = "done"
    except Exception as e:
        sess["status"] = "error"
        sess["error"], sess["error_kind"] = _lxm_friendly_error(e); sess["error_detail"] = str(e)
        print(f"[lxm] match error: {e}")
    finally:
        sess["thinking"] = ""; sess["building"] = ""
        _save_field_session(sess)


def _run_lxm_creature_match(sid: str, game: str, creature_paths: list, kind: str):
    """Two creatures MEET on the hosted LxM arena (D-089 §5, B1). Each plays on an
    ephemeral copy (D-090); on a `published` encounter BOTH remember it — a reflection
    + a bond toward the other keyed on its stable creature_id (re-recognition on a
    re-meeting). This is the cross-machine encounter the field exists for."""
    import time as _time
    from ludex.bridges.hosted_match import play_creature_match, record_encounter, recognize
    sess = field_sessions[sid]
    pa, pb = creature_paths[0], creature_paths[1]
    na = os.path.basename(str(pa).rstrip("/\\"))
    nb = os.path.basename(str(pb).rstrip("/\\"))
    try:
        max_turns = {"trustgame": 12, "tictactoe": 9}.get(game, 16)
        field = LxMField(na, nb)
        sess["field"] = field
        sess["entered"] = [na, nb]; sess["building"] = ""
        sess["status"] = "running"; sess["building"] = "the Ludus ex Machina arena"; sess["thinking"] = ""

        def on_turn(rec):
            field.add_turn({"turn": rec.get("turn"), "who": rec.get("name"),
                            "move": rec.get("move"), "dialogue": rec.get("dialogue")})
            sess["thinking"] = rec.get("name")

        result = play_creature_match(pa, pb, game=game, kind=kind, max_turns=max_turns,
                                     on_turn=on_turn, on_start=lambda u: sess.update(viewer_url=u, building=""),
                                     should_stop=lambda: sess.get("stop"))
        field.result = result.get("result")
        field.viewer_url = result.get("viewer_url")
        sess["result"] = result.get("result")
        sess["viewer_url"] = result.get("viewer_url")
        sess["scores"] = (result.get("result") or {}).get("scores")
        if sess.get("stop"):
            sess["status"] = "stopped"
        else:
            if kind == "published":      # both remember the encounter (reflection + B1 re-recognition bond)
                creatures = result.get("creatures", {})
                summary = (result.get("result") or {}).get("summary", "")
                when = _time.strftime("%Y-%m-%d")
                sess["status"] = "reflecting"
                for path, name in ((pa, na), (pb, nb)):
                    c = creatures.get(name, {})
                    oppid, oppname = c.get("opponent_id"), c.get("opponent")
                    sess["aftermath"] = f"reflect:{name}"
                    prior = recognize(path, oppid) if oppid else None
                    sess["aftermath"] = f"bond:{name}->{oppname}"
                    record_encounter(path, oppname, oppid, summary, game, when, prior=prior)
                sess["aftermath"] = ""
            sess["status"] = "done"
    except Exception as e:
        sess["status"] = "error"
        sess["error"], sess["error_kind"] = _lxm_friendly_error(e); sess["error_detail"] = str(e)
        print(f"[lxm] match error: {e}")
    finally:
        sess["thinking"] = ""; sess["building"] = ""
        _save_field_session(sess)


class _StopField(Exception):
    """Raised inside response_fn when the user asks to stop — aborts the run at the
    next turn boundary (a brain call already in flight still has to return first)."""


def _field_transcript_text(field) -> str:
    lines = []
    for rd in field.rounds:
        for rec in rd.records:
            lines.append(f"[{rec.phase}] {rec.participant}: {rec.content}")
    return "In a field session you took part in, this was discussed:\n" + "\n".join(lines)


def _field_aftermath(sess, field, field_kind, text):
    """After a field session finishes, leave durable traces (JJ): each participant
    reflects on it (→ SELF.md) and writes a bond toward each other participant
    (→ bonds/). Like people — doing something together leaves memories and brings you
    closer; repeated sessions deepen the bond (update_bond accumulates)."""
    from ludex.core import selfhood
    parts = [p for p in field.participants if getattr(p, "organism", None)]
    transcript = _field_transcript_text(field)
    label = "a Forum on the claim" if field_kind == "forum" else "a Council on the dilemma"
    summary = f'{label}: "{text}"'
    # Progress counters for the observe UI: the aftermath is N reflects +
    # N×(N-1) bond writeups, each a brain call — minutes of work that
    # otherwise looks like a hang ("reflecting" sat unchanged for 5 min
    # in the 2026-06-11 public Forum usability test).
    aftermath_total = len(parts) + len(parts) * (len(parts) - 1)
    aftermath_i = 0
    sess["aftermath_n"] = aftermath_total
    for p in parts:                       # reflection → SELF.md
        if sess.get("stop"):
            return
        aftermath_i += 1
        sess["aftermath_i"] = aftermath_i
        sess["aftermath"] = f"reflect:{p.name}"
        try:
            selfhood.reflect(p.organism, field_kind, p.engine, transcript)
        except Exception as e:
            print(f"field reflect failed for {p.name}: {e}")
    for p in parts:                       # bond writeup → bonds/<other>.md (both ways)
        for q in parts:
            if p is q:
                continue
            if sess.get("stop"):
                return
            aftermath_i += 1
            sess["aftermath_i"] = aftermath_i
            sess["aftermath"] = f"bond:{p.name}->{q.name}"
            try:
                qmodel = ""
                try:
                    qmodel = q.organism.config.get("model", "") if q.organism else ""
                except Exception:
                    pass
                selfhood.update_bond(p.organism, q.name,
                                     shared_experience=f"You took part in {summary}, alongside {q.name}.",
                                     other_brain=qmodel)
            except Exception as e:
                print(f"field bond writeup failed {p.name}->{q.name}: {e}")
    sess["aftermath"] = ""


def _run_field_bg(sid: str, field_kind: str, text: str, creature_paths: list, mediator: str = "",
                  ticks: int = 10):
    """Background worker: build the chosen field, admit creatures, run it. Transcript
    accumulates in the field object (polled). Progress (waking/entered/thinking) is
    surfaced so the UI isn't a black box — brain calls (incl. the D-072 probe at first
    build) are slow. Council = dilemma debate; Forum = claim verification (we run the
    debate rounds in observation mode, skipping the ground-truth verdict)."""
    sess = field_sessions[sid]
    try:
        from ludex.fields.conversation import Participant
        if field_kind == "wilderness":
            from ludex.fields.wilderness import Wilderness

            def _wild_progress(stage, detail):
                if stage == "tick":
                    sess["tick"] = detail                  # "3/10:storm"
                elif stage == "thinking":
                    sess["thinking"] = detail
                elif stage == "aftermath":
                    sess["aftermath"] = detail
                    if detail:                             # aftermath runs INSIDE wild.run()
                        sess["status"] = "reflecting"

            field = Wilderness(name=f"web-wild-{sid}", total_ticks=ticks,
                               auto_tom=True, auto_trace=True,
                               progress_cb=_wild_progress,
                               stop_check=lambda: bool(sess.get("stop")))
        elif field_kind == "forum":
            from ludex.fields.forum import Forum, ForumClaim
            field = Forum(name=f"web-forum-{sid}", claim=ForumClaim(text=text), verdict=None, auto_trace=False)
        else:
            from ludex.fields.council import Council, Dilemma
            field = Council(name=f"web-council-{sid}", dilemma=Dilemma(text=text), auto_trace=False)
        sess["field"] = field
        for path in creature_paths:
            name0 = os.path.basename(str(path).rstrip("/\\"))
            sess["building"] = name0          # "waking <name>…" (build may probe the brain)
            org = _build_creature_org(path)
            name = getattr(org, "name", None) or name0
            if field_kind == "wilderness":
                field.join(org)
            else:
                role = "mediator" if (field_kind == "council" and mediator and mediator in (name, name0)) else "discussant"
                field.add_participant(Participant(name=name, role=role,
                                                  organism=org, engine=org.get_block("engine")))
            sess["entered"].append(name)
            sess["building"] = ""

        def response_fn(p, prompt):
            if sess.get("stop"):
                raise _StopField()
            sess["thinking"] = p.name          # "<name> is thinking…"
            try:
                r = p.engine.handle_submit(prompt)
                return (r.response or "").strip() or "[no response]"
            except _StopField:
                raise
            except Exception as e:
                return f"[error: {e}]"
            finally:
                sess["thinking"] = ""

        sess["status"] = "running"
        if field_kind == "wilderness":
            # Wilderness runs its own loop (engines called directly) and its
            # own aftermath (_finish: memory/bonds/ToM/physis/reflect/dream) —
            # progress surfaces via the callback; no _field_aftermath here.
            field.run()
        elif field_kind == "forum":
            field.post_claim()
            field.confidence_round(response_fn)
            field.evidence_round(response_fn)
            field.challenge_round(response_fn)
            field.update_round(response_fn)
        else:
            field.run(response_fn)
        if field_kind != "wilderness":
            sess["status"] = "reflecting"      # post-session: durable memory + bonds (JJ)
            _field_aftermath(sess, field, field_kind, text)
        sess["status"] = "stopped" if sess.get("stop") else "done"
    except _StopField:
        sess["status"] = "stopped"
    except Exception as e:
        sess["status"] = "error"
        sess["error"] = str(e)
    finally:
        sess["building"] = ""
        sess["thinking"] = ""
        sess["aftermath"] = ""
        _save_field_session(sess)   # persist for history (survives restart)


def _run_lxm_multi_match_bg(sid: str, game: str, creature_paths: list, kind: str):
    """N creatures meet on the hosted LxM arena (D-089 §5, B1) — the N-seat generalization of
    _run_lxm_creature_match (avalon, codenames, blockworld, deduction-solo, …). Each plays on
    an ephemeral copy (D-090); on a `published` match each remembers it (a reflection + a bond
    toward every co-participant, re-recognizable via its stable B1 id)."""
    import time as _time
    from ludex.bridges.hosted_match import play_multi_creature_match, record_multi_encounter
    sess = field_sessions[sid]
    names = [os.path.basename(str(p).rstrip("/\\")) for p in creature_paths]
    try:
        max_turns = {"avalon": 120, "codenames": 60, "blockworld": 40}.get(game, 30)
        field = LxMField(names[0]); field.participant_names = list(names)
        sess["field"] = field
        sess["entered"] = list(names); sess["building"] = ""
        sess["status"] = "running"; sess["building"] = "the Ludus ex Machina arena"; sess["thinking"] = ""

        def on_turn(rec):
            field.add_turn({"turn": rec.get("turn"), "who": rec.get("name"),
                            "move": rec.get("move"), "dialogue": rec.get("dialogue")})
            sess["thinking"] = rec.get("name")

        result = play_multi_creature_match(creature_paths, game=game, kind=kind, max_turns=max_turns,
                                           on_turn=on_turn, on_start=lambda u: sess.update(viewer_url=u, building=""),
                                           should_stop=lambda: sess.get("stop"))
        field.result = result.get("result")
        field.viewer_url = result.get("viewer_url")
        sess["result"] = result.get("result")
        sess["viewer_url"] = result.get("viewer_url")
        sess["scores"] = (result.get("result") or {}).get("scores")
        if sess.get("stop"):
            sess["status"] = "stopped"
        else:
            if kind == "published":      # each creature remembers the match (reflection + per-peer B1 bond)
                creatures = result.get("creatures", {})
                summary = (result.get("result") or {}).get("summary", "")
                when = _time.strftime("%Y-%m-%d")
                sess["status"] = "reflecting"
                for path, name in zip(creature_paths, names):
                    co = creatures.get(name, {}).get("co_participants", [])
                    sess["aftermath"] = f"reflect:{name}"
                    record_multi_encounter(path, co, summary, game, when)
                sess["aftermath"] = ""
            sess["status"] = "done"
    except Exception as e:
        sess["status"] = "error"
        sess["error"], sess["error_kind"] = _lxm_friendly_error(e); sess["error_detail"] = str(e)
        print(f"[lxm] multi match error: {e}")
    finally:
        sess["thinking"] = ""; sess["building"] = ""
        _save_field_session(sess)


class FieldStartRequest(BaseModel):
    field: str = "council"
    dilemma: str = ""
    creatures: list = []   # habitat paths
    mediator: str = ""     # optional: name of the creature to seat as mediator
    ticks: int = 10        # wilderness only: how long the world runs
    game: str = ""         # lxm only: which game in the hosted arena
    kind: str = "practice" # lxm only: practice (ephemeral) | published (permanent + viewable)


class FieldVerdictRequest(BaseModel):
    value: str = ""        # true | false | partial | unknown
    note: str = ""         # caretaker rationale, shown with the verdict


@app.get("/api/fields")
async def fields_registry():
    """The field taxonomy the Field tab renders — the internal fields.
    Declarative; add a field by editing FIELD_REGISTRY."""
    return FIELD_REGISTRY


@app.post("/api/lxm/warm")
async def lxm_warm():
    """Wake the hosted LxM arena in the background (Render free idles when quiet) so a match
    the user is about to start doesn't wait ~30s for a cold start. Fired when the Field LxM
    setup opens — by the time they pick a game + creatures, the arena is warm."""
    import threading
    import urllib.request
    from ludex.bridges.hosted_match import ONRENDER

    def _ping():
        try:
            urllib.request.urlopen(ONRENDER + "/api/games", timeout=60)
        except Exception:
            pass
    threading.Thread(target=_ping, daemon=True).start()
    return {"ok": True}


@app.get("/api/lxm/games")
async def lxm_games():
    """The arena's playable games + player ranges (live, cached) for the Field roster.
    Each: {id, name, min, max} — the frontend renders a card per game and validates the
    admitted-creature count against [min, max]."""
    from ludex.bridges.hosted_match import HOUSE_BOTS
    return {"games": [{"id": g["id"], "name": _LXM_GAME_NAMES.get(g["id"], g["id"]),
                       "min": g.get("min_players", 2), "max": g.get("max_players", 2),
                       "house": g["id"] in HOUSE_BOTS}
                      for g in _lxm_games()]}


@app.post("/api/field/start")
async def field_start(req: FieldStartRequest):
    if req.field == "lxm":
        from ludex.bridges.hosted_match import HOUSE_BOTS   # games with a scripted house opponent
        ranges = {g["id"]: (g.get("min_players", 2), g.get("max_players", 2)) for g in _lxm_games()}
        if req.game not in ranges:
            return {"error": f"Game '{req.game}' is not in the LxM arena."}
        gmin, gmax = ranges[req.game]
        n = len(req.creatures)
        has_house = req.game in HOUSE_BOTS
        allow_min = 1 if has_house else gmin     # a house-bot game can be played solo vs the house
        if n < allow_min or n > gmax:
            need = f"{gmin}" if gmin == gmax else f"{gmin}–{gmax}"
            extra = " (or 1 vs the house)" if has_house else ""
            return {"error": f"'{req.game}' needs {need} creatures{extra} — you admitted {n}."}
        import threading
        sid = f"f{int(time.time() * 1000) % 1000000}"
        names = " vs ".join(os.path.basename(str(p).rstrip("/\\")) for p in req.creatures)
        field_sessions[sid] = {"sid": sid, "field": None, "status": "starting", "error": "",
                               "field_kind": "lxm", "dilemma": f"LxM · {req.game} · {names} ({req.kind})",
                               "game": req.game, "kind": req.kind, "entered": [], "building": "",
                               "thinking": "", "stop": False, "started": time.time(),
                               "mediator": "", "aftermath": "", "tick": ""}
        if n == 1 and has_house:
            threading.Thread(target=_run_lxm_match_bg,
                             args=(sid, req.game, req.creatures[0], req.kind), daemon=True).start()
        elif n == 2:
            threading.Thread(target=_run_lxm_creature_match,
                             args=(sid, req.game, req.creatures, req.kind), daemon=True).start()
        else:
            threading.Thread(target=_run_lxm_multi_match_bg,
                             args=(sid, req.game, req.creatures, req.kind), daemon=True).start()
        return {"session_id": sid, "status": "starting"}
    if req.field not in ("council", "forum", "wilderness"):
        return {"error": f"Field '{req.field}' is not supported yet."}
    ticks = max(3, min(int(req.ticks or 10), 30))
    if req.field == "wilderness":
        if len(req.creatures) < 1:
            return {"error": "Admit at least 1 creature."}
        dilemma = f"Wilderness · {ticks} ticks"     # history label (no claim text)
    else:
        if not (req.dilemma or "").strip():
            return {"error": "A dilemma/claim is required."}
        if len(req.creatures) < 2:
            return {"error": "Admit at least 2 creatures."}
        dilemma = req.dilemma
    import threading
    sid = f"f{int(time.time() * 1000) % 1000000}"
    field_sessions[sid] = {"sid": sid, "field": None, "status": "starting", "error": "", "field_kind": req.field,
                           "dilemma": dilemma, "entered": [], "building": "", "thinking": "", "stop": False,
                           "started": time.time(), "mediator": req.mediator, "aftermath": "", "tick": ""}
    threading.Thread(target=_run_field_bg, args=(sid, req.field, dilemma, req.creatures, req.mediator, ticks), daemon=True).start()
    return {"session_id": sid, "status": "starting"}


@app.get("/api/field/sessions")
async def field_sessions_list():
    """All sessions — live (in-memory) + finished (on disk), newest first."""
    out = {}
    try:
        for fn in sorted(os.listdir(FIELD_LOG)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(FIELD_LOG, fn), encoding="utf-8") as f:
                    d = json.load(f)
                out[d.get("sid", fn[:-5])] = {
                    "sid": d.get("sid", fn[:-5]), "status": d.get("status"), "dilemma": d.get("dilemma", ""),
                    "field": d.get("field_kind", "council"),
                    "participants": d.get("participants", []), "started": d.get("started", 0), "live": False,
                    "turns": _count_turns(d.get("transcript", []))}
            except Exception:
                pass
    except FileNotFoundError:
        pass
    for sid, sess in field_sessions.items():    # live overrides disk
        try:
            field = sess.get("field")
            out[sid] = {"sid": sid, "status": sess.get("status"), "dilemma": sess.get("dilemma", ""),
                        "field": sess.get("field_kind", "council"),
                        "participants": _field_participant_names(field) or sess.get("entered", []),
                        "started": sess.get("started", 0), "live": True,
                        "turns": _count_turns(_session_transcript_records(field))}
        except Exception:
            pass
    return {"sessions": sorted(out.values(), key=lambda s: s.get("started", 0), reverse=True)}


@app.get("/api/field/session/{sid}")
async def field_session(sid: str):
    sess = field_sessions.get(sid)
    if not sess:                                 # finished — load from disk
        try:
            with open(os.path.join(FIELD_LOG, f"{sid}.json"), encoding="utf-8") as f:
                d = json.load(f)
            return {"status": d.get("status"), "error": "", "field": d.get("field_kind"),
                    "participants": d.get("participants", []), "transcript": d.get("transcript", []),
                    "viewer_url": d.get("viewer_url"), "result": d.get("result") or d.get("scores"), "game": d.get("game", ""),
                    "entered": d.get("participants", []), "building": "", "thinking": "",
                    "mediator": d.get("mediator", ""), "aftermath": "",
                    "verdict": d.get("verdict"), "scores": d.get("scores"),
                    "panel": (_panel_from_transcript(d.get("transcript", []))
                              if d.get("field_kind") == "forum" else None),
                    "elapsed": int(d.get("ended", 0) - d.get("started", 0)) if d.get("started") else 0,
                    "turns": sum(1 for r in d.get("transcript", []) if r.get("phase") != "dilemma_posed")}
        except Exception:
            return {"error": "Session not found"}
    field = sess.get("field")
    transcript = _session_transcript_records(field)
    participants = _field_participant_names(field)
    started = sess.get("started", 0)
    return {"status": sess.get("status"), "error": sess.get("error", ""),
            "field": sess.get("field_kind"), "participants": participants, "transcript": transcript,
            "viewer_url": sess.get("viewer_url"), "result": sess.get("result"), "game": sess.get("game", ""),
            "error_kind": sess.get("error_kind", ""),
            "entered": sess.get("entered", []), "building": sess.get("building", ""),
            "thinking": sess.get("thinking", ""), "mediator": sess.get("mediator", ""),
            "aftermath": sess.get("aftermath", ""), "tick": sess.get("tick", ""),
            "aftermath_i": sess.get("aftermath_i", 0), "aftermath_n": sess.get("aftermath_n", 0),
            "verdict": sess.get("verdict"), "scores": sess.get("scores"),
            "panel": (_panel_aggregate(getattr(field, "_latest_stance", {}) or {})
                      if sess.get("field_kind") == "forum" and field is not None else None),
            "elapsed": int(time.time() - started) if started else 0,
            "turns": sum(1 for r in transcript if r["phase"] != "dilemma_posed")}


_STANCE_RE = None


def _parse_stance(content: str):
    """Extract (stance, confidence) from a position/update record's text.
    Creatures format these as 'STANCE: false / CONFIDENCE: 0.7' per the
    forum prompt contract."""
    global _STANCE_RE
    import re as _re
    if _STANCE_RE is None:
        _STANCE_RE = (_re.compile(r"STANCE:\s*\**\s*(true|false|partial)", _re.I),
                      _re.compile(r"CONFIDENCE:\s*\**\s*([01](?:\.\d+)?)", _re.I))
    s = _STANCE_RE[0].search(content or "")
    c = _STANCE_RE[1].search(content or "")
    return (s.group(1).lower() if s else None,
            float(c.group(1)) if c else None)


def _panel_aggregate(stances: dict) -> dict | None:
    """Aggregate final stances into the panel verdict (D-087 source 1).

    Collective JUDGMENT, not ground truth — rendered with that label and
    firewalled from accuracy rewards. stances: name -> (stance, conf|None).
    """
    counts: dict = {}
    weights: dict = {}
    for _name, (stance, conf) in stances.items():
        s = str(stance or "").lower()
        if s not in ("true", "false", "partial"):
            continue
        counts[s] = counts.get(s, 0) + 1
        weights[s] = weights.get(s, 0.0) + (conf if conf is not None else 0.5)
    if not counts:
        return None
    lead = max(weights, key=lambda k: weights[k])
    return {"counts": counts,
            "weighted": {k: round(v, 2) for k, v in weights.items()},
            "lead": lead}


def _panel_from_transcript(transcript: list) -> dict | None:
    final = {}
    for r in transcript:
        if r.get("kind") in ("position", "position_updated"):
            stance, conf = _parse_stance(r.get("content", ""))
            if stance:
                final[r.get("participant")] = (stance, conf)
    return _panel_aggregate(final) if final else None


@app.post("/api/field/verdict/{sid}")
async def field_verdict(sid: str, req: FieldVerdictRequest):
    """Caretaker discloses the ground truth for a finished Forum session.

    Live session (still in server memory — the normal case, right after it
    finishes): full ForumScore per participant via the field's own scoring.
    Disk-only session (after a restart): the verdict is recorded and each
    participant's FINAL stance is matched against it (hit/near/miss) —
    simplified feedback, honestly labeled.
    """
    if req.value not in ("true", "false", "partial", "unknown"):
        return {"error": "value must be true|false|partial|unknown"}

    sess = field_sessions.get(sid)
    if sess is not None and sess.get("field") is not None:
        if sess.get("field_kind") != "forum":
            return {"error": "verdict applies to forum sessions only"}
        if sess.get("verdict"):
            return {"error": "verdict already disclosed"}
        from ludex.core.verdict import Verdict
        field = sess["field"]
        try:
            field.disclose_verdict(Verdict(value=req.value,
                                           provenance="caretaker:web",
                                           explanation=req.note or ""))
            scores = field.score()
            sess["scores"] = {name: vars(s) for name, s in scores.items()}
        except Exception as e:
            return {"error": f"scoring failed: {e}"}
        sess["verdict"] = {"value": req.value, "note": req.note,
                           "scored": "full"}
        _save_field_session(sess)
        return {"verdict": sess["verdict"], "scores": sess["scores"]}

    # disk-only fallback
    path = os.path.join(FIELD_LOG, f"{sid}.json")
    if not os.path.isfile(path):
        return {"error": "Session not found"}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if d.get("field_kind") != "forum":
        return {"error": "verdict applies to forum sessions only"}
    if d.get("verdict"):
        return {"error": "verdict already disclosed"}
    final = {}
    for r in d.get("transcript", []):
        if r.get("kind") in ("position", "position_updated"):
            stance, conf = _parse_stance(r.get("content", ""))
            if stance:
                final[r.get("participant")] = {"stance": stance, "confidence": conf}
    results = {}
    for name, fs in final.items():
        if req.value == "unknown":
            match = "n/a"
        elif fs["stance"] == req.value:
            match = "hit"
        elif "partial" in (fs["stance"], req.value):
            match = "near"
        else:
            match = "miss"
        results[name] = {**fs, "match": match}
    d["verdict"] = {"value": req.value, "note": req.note, "scored": "simplified"}
    d["scores"] = results
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    return {"verdict": d["verdict"], "scores": results}


@app.post("/api/field/stop/{sid}")
async def field_stop(sid: str):
    """Ask a running session to stop. It aborts at the next turn boundary; a brain
    call already in flight must return/time out first (to truly kill a hung call,
    restart the server — the daemon thread dies with it)."""
    sess = field_sessions.get(sid)
    if not sess:
        return {"error": "Session not found"}
    sess["stop"] = True
    if sess.get("status") in ("starting", "running"):
        sess["status"] = "stopping"
    return {"status": sess.get("status")}


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


@app.get("/api/model-hints")
async def model_hints():
    """Current models per provider — single source of truth shared with the CLI
    (ludex.cli.PROVIDER_MODEL_HINTS), so the Forge dropdowns never drift stale."""
    try:
        from ludex.cli import PROVIDER_MODEL_HINTS
        return {"hints": {p: [m.strip() for m in h.split("|") if m.strip()]
                          for p, h in PROVIDER_MODEL_HINTS.items()}}
    except Exception as e:
        return {"hints": {}, "error": str(e)}


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
        _enable_web_multiturn(org)
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
        _enable_web_multiturn(org)
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
    # Sleep = reflect once. The creature looks back on the session and authors
    # SELF.md — the designed durable cross-session continuity (D-021/D-044), which
    # raw memory + keyword recall don't provide. Best-effort, and only when
    # something was actually said this session.
    org = agents.get(session_id)
    reflected = False
    if org is not None:
        try:
            engine = org.get_block("engine")
            if engine and getattr(engine, "_turn_count", 0) >= 1:
                convo = _session_transcript(engine)
                # One episodic memory per conversation SESSION (D-024/F1:
                # session granularity, not per-turn). The reflection below
                # carries the meaning; this carries the episode.
                memory = org.get_block("memory")
                if memory and convo:
                    try:
                        turns = getattr(engine, "_turn_count", 0)
                        memory.handle_remember(
                            content=(f"A conversation with the user "
                                     f"({turns} turn(s)). " + convo[:500]),
                            memory_type="episodic",
                            tags=["conversation", "web_chat"],
                            importance=IMPORTANCE_SESSION_CHAT,
                            source="web_chat/session",
                        )
                    except Exception as e:
                        print(f"session memory capture failed: {e}")
                from ludex.core import selfhood
                text = await asyncio.to_thread(selfhood.reflect, org, "sleep", engine, convo)
                reflected = bool(text)
        except Exception as e:
            print(f"sleep reflection failed: {e}")
    if session_id in agents:
        del agents[session_id]
    if session_id in agent_types:
        del agent_types[session_id]
    return {"status": "disconnected", "reflected": reflected}


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
