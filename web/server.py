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

    # Capture the exchange as episodic memory. This is lived experience (the content
    # of a conversation), NOT the turn-boundary telemetry that D-024 removed from
    # auto-capture — so it belongs in memory. Gives the creature durable, recall-able
    # conversational continuity across turns AND sessions (the channel the rest of
    # the system uses), complementing the session-scoped history flatten.
    memory = org.get_block("memory")
    if memory and result.response and not result.error:
        try:
            memory.handle_remember(
                content=f'In conversation, the user said: "{req.message}" — I replied: "{result.response}"',
                memory_type="episodic",
                tags=["conversation", "web_chat"],
                importance=0.5,
                source="web_chat",
            )
        except Exception as e:
            print(f"chat memory capture failed: {e}")

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
# Field sessions — admit creatures into a field, run it, observe (background)
# ============================================================
field_sessions = {}  # sid -> {field, status, error, field_kind}  (in-memory, live)
FIELD_LOG = os.path.join(REPO_ROOT, "field_log")  # finished sessions persisted here (survive restarts)


def _session_transcript_records(field):
    out = []
    if field is not None:
        for rd in field.rounds:
            for rec in rd.records:
                out.append({"round": rec.round_index, "phase": rec.phase,
                            "participant": rec.participant, "kind": rec.kind, "content": rec.content})
    return out


def _save_field_session(sess):
    """Persist a finished session to disk so it survives server restarts (history)."""
    try:
        os.makedirs(FIELD_LOG, exist_ok=True)
        field = sess.get("field")
        data = {
            "sid": sess.get("sid"), "field_kind": sess.get("field_kind"), "status": sess.get("status"),
            "dilemma": sess.get("dilemma", ""), "mediator": sess.get("mediator", ""),
            "participants": [p.name for p in field.participants] if field else sess.get("entered", []),
            "started": sess.get("started", 0), "ended": time.time(),
            "transcript": _session_transcript_records(field),
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


class _StopField(Exception):
    """Raised inside response_fn when the user asks to stop — aborts the run at the
    next turn boundary (a brain call already in flight still has to return first)."""


def _council_transcript_text(council) -> str:
    lines = []
    for rd in council.rounds:
        for rec in rd.records:
            lines.append(f"[{rec.phase}] {rec.participant}: {rec.content}")
    return "In a Council you took part in, this was discussed:\n" + "\n".join(lines)


def _council_aftermath(sess, council, dilemma_text):
    """After a council finishes, leave durable traces (JJ): each participant reflects
    on it (→ SELF.md) and writes a bond toward each other participant (→ bonds/).
    Like people — doing something together leaves memories and brings you closer;
    repeated councils deepen the bond (update_bond accumulates)."""
    from ludex.core import selfhood
    parts = [p for p in council.participants if getattr(p, "organism", None)]
    transcript = _council_transcript_text(council)
    summary = f'a Council debating the dilemma: "{dilemma_text}"'
    for p in parts:                       # reflection → SELF.md
        if sess.get("stop"):
            return
        sess["aftermath"] = f"reflect:{p.name}"
        try:
            selfhood.reflect(p.organism, "council", p.engine, transcript)
        except Exception as e:
            print(f"council reflect failed for {p.name}: {e}")
    for p in parts:                       # bond writeup → bonds/<other>.md (both ways)
        for q in parts:
            if p is q:
                continue
            if sess.get("stop"):
                return
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
                print(f"council bond writeup failed {p.name}->{q.name}: {e}")
    sess["aftermath"] = ""


def _run_council_bg(sid: str, dilemma_text: str, creature_paths: list, mediator: str = ""):
    """Background worker: build a Council, admit the chosen creatures, run it. The
    transcript accumulates in the field object, polled via /api/field/session.
    Progress (waking / entered / thinking) is surfaced so the UI isn't a black box —
    brain calls (incl. the D-072 capability probe at first build) are slow."""
    sess = field_sessions[sid]
    try:
        from ludex.fields.council import Council, Dilemma
        from ludex.fields.conversation import Participant
        council = Council(name=f"web-council-{sid}", dilemma=Dilemma(text=dilemma_text), auto_trace=False)
        sess["field"] = council
        for path in creature_paths:
            name0 = os.path.basename(str(path).rstrip("/\\"))
            sess["building"] = name0          # "waking <name>…" (build may probe the brain)
            org = _build_creature_org(path)
            name = getattr(org, "name", None) or name0
            role = "mediator" if mediator and mediator in (name, name0) else "discussant"
            council.add_participant(Participant(name=name, role=role,
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
        council.run(response_fn)
        sess["status"] = "reflecting"      # post-council: durable memory + bonds (JJ)
        _council_aftermath(sess, council, dilemma_text)
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


class FieldStartRequest(BaseModel):
    field: str = "council"
    dilemma: str = ""
    creatures: list = []   # habitat paths
    mediator: str = ""     # optional: name of the creature to seat as mediator


@app.post("/api/field/start")
async def field_start(req: FieldStartRequest):
    if req.field != "council":
        return {"error": f"Field '{req.field}' is not supported yet."}
    if not (req.dilemma or "").strip():
        return {"error": "A dilemma is required."}
    if len(req.creatures) < 2:
        return {"error": "Admit at least 2 creatures."}
    import threading
    sid = f"f{int(time.time() * 1000) % 1000000}"
    field_sessions[sid] = {"sid": sid, "field": None, "status": "starting", "error": "", "field_kind": "council",
                           "dilemma": req.dilemma, "entered": [], "building": "", "thinking": "", "stop": False,
                           "started": time.time(), "mediator": req.mediator, "aftermath": ""}
    threading.Thread(target=_run_council_bg, args=(sid, req.dilemma, req.creatures, req.mediator), daemon=True).start()
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
                    "participants": d.get("participants", []), "started": d.get("started", 0), "live": False,
                    "turns": sum(1 for r in d.get("transcript", []) if r.get("phase") != "dilemma_posed")}
            except Exception:
                pass
    except FileNotFoundError:
        pass
    for sid, sess in field_sessions.items():    # live overrides disk
        field = sess.get("field")
        out[sid] = {"sid": sid, "status": sess.get("status"), "dilemma": sess.get("dilemma", ""),
                    "participants": [p.name for p in field.participants] if field else sess.get("entered", []),
                    "started": sess.get("started", 0), "live": True,
                    "turns": sum(1 for rd in (field.rounds if field else []) for rec in rd.records
                                 if rec.phase != "dilemma_posed")}
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
                    "entered": d.get("participants", []), "building": "", "thinking": "",
                    "mediator": d.get("mediator", ""), "aftermath": "",
                    "elapsed": int(d.get("ended", 0) - d.get("started", 0)) if d.get("started") else 0,
                    "turns": sum(1 for r in d.get("transcript", []) if r.get("phase") != "dilemma_posed")}
        except Exception:
            return {"error": "Session not found"}
    transcript, participants = [], []
    field = sess.get("field")
    if field is not None:
        participants = [p.name for p in field.participants]
        for rd in field.rounds:
            for rec in rd.records:
                transcript.append({"round": rec.round_index, "phase": rec.phase,
                                   "participant": rec.participant, "kind": rec.kind,
                                   "content": rec.content})
    started = sess.get("started", 0)
    return {"status": sess.get("status"), "error": sess.get("error", ""),
            "field": sess.get("field_kind"), "participants": participants, "transcript": transcript,
            "entered": sess.get("entered", []), "building": sess.get("building", ""),
            "thinking": sess.get("thinking", ""), "mediator": sess.get("mediator", ""),
            "aftermath": sess.get("aftermath", ""),
            "elapsed": int(time.time() - started) if started else 0,
            "turns": sum(1 for r in transcript if r["phase"] != "dilemma_posed")}


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
