# Ludex Architecture — Inter-Organ Communication System

*🌐 [English](ARCHITECTURE.md) · [한국어](ARCHITECTURE.ko.md)*

> "Before you can attach and detach organs, you first need a communication
> system between them — the vessels and nerves."

---

## 1. Core design principles

### The biological analogy

Why organs work together organically in a real organism:
- **Circulatory**: delivers nutrients (data) to every organ and collects waste (errors)
- **Nervous**: event-driven signaling — "food arrived from above" → notify the small intestine
- **Endocrine**: global settings — "we're under stress" → change every organ's behavior
- **Connective tissue**: physically holds organs in place, but as removable connections

Ludex implements these four as software.

---

## 2. Inter-organ communication — four channels

### 2.1 Bus (circulatory = data flow)

A data pipeline shared by every block. It moves data between blocks.

```python
from ludex.core import Bus

bus = Bus()

# The Provider streams a response onto the Bus
bus.publish("llm.response", {
    "model": "exaone3.5:7.8b",
    "content": "The next move is e4.",
    "tokens": {"input": 150, "output": 30}
})

# The Tracking block subscribes to token usage
# The Engine block subscribes to response content
# Both receive the same data but use only the part they need
```

**Design:**
- Pub/Sub pattern
- Topic-based routing: `llm.response`, `tool.executed`, `session.saved`, `error.occurred`
- Blocks can also be called directly without the Bus (the Bus is optional)
- Both synchronous and asynchronous supported

```python
class Bus:
    """The circulatory system that manages data flow between organs"""

    def subscribe(self, topic: str, handler: Callable) -> Subscription:
        """Subscribe to data on a given topic"""

    def publish(self, topic: str, data: Any) -> None:
        """Publish data to a topic (delivered to all subscribers)"""

    def unsubscribe(self, subscription: Subscription) -> None:
        """Unsubscribe (when an organ is removed)"""
```

### 2.2 Signals (nervous = event notification)

Event signals between blocks. Unlike the Bus, this is not data delivery but a
notification that "something happened."

```python
from ludex.core import Signals

signals = Signals()

# The Engine emits a turn-start signal
signals.emit("turn.started", turn_number=5)

# The Hooks block receives it and runs before_turn handling
# The Tracking block receives it and starts a timer
# The Memory block receives it and recalls relevant memories
```

**Bus vs Signals:**
- Bus = data flow (blood). Large payloads. "Handle this response."
- Signals = event notification (nerves). Small signals. "A turn started."

```python
class Signals:
    """The nervous system that manages event notifications between organs"""

    def emit(self, event: str, **kwargs) -> None:
        """Notify that an event occurred"""

    def on(self, event: str, handler: Callable) -> None:
        """Register an event listener"""

    def off(self, event: str, handler: Callable) -> None:
        """Remove an event listener"""
```

**Standard event list:**
| Event | Emitting block | Receiving block | Description |
|--------|----------|----------|------|
| `session.started` | Engine | All | New session begins |
| `session.ended` | Engine | Tracking, Memory | Session ends |
| `turn.started` | Engine | Hooks, Tracking | Turn begins |
| `turn.ended` | Engine | Hooks, Tracking, Memory | Turn ends |
| `llm.calling` | Provider | Resilience, Tracking | LLM API call starts |
| `llm.responded` | Provider | Engine, Tracking | LLM response received |
| `llm.failed` | Provider | Resilience | LLM call failed |
| `tool.calling` | Registry | Hooks | Tool execution starts |
| `tool.executed` | Registry | Hooks, Tracking | Tool execution complete |
| `context.compacted` | Engine | Tracking | Context compaction occurred |
| `memory.recalled` | Memory | Engine | Memory recall complete |
| `error.occurred` | Any | Resilience, Tracking | Error occurred |
| `config.changed` | Config | All | Config changed (hormone) |

### 2.3 Config (endocrine = global settings)

A hormone system that globally tunes the behavior of every block.

```python
from ludex.core import Config

config = Config({
    "model": "exaone3.5:7.8b",
    "token_budget": 4000,
    "temperature": 0.7,
    "max_turns": 50,
    "retry_max": 3,
    "verbose": False,
})

# Read settings from any block
budget = config.get("token_budget")

# Changing a setting signals every block
config.set("model", "mistral:7b")  # → Signals.emit("config.changed", key="model")
```

**Design:**
- Read: `config.get(key)` from anywhere
- Write: `config.set(key, value)` → automatically emits a `config.changed` signal
- Layering: defaults → project config → session override (inspired by OpenClaw's 5-layer model)
- Immutable snapshot: `config.snapshot()` → returns a frozen dict (for experiment reproducibility)

### 2.4 Ports (connective tissue = block interface)

The standard interface by which each block connects to others. Like a USB port.

```python
from ludex.core import Block, Port

class EngineBlock(Block):
    """The session/context-management organ"""

    # Ports this block provides (other blocks may connect)
    provides = [
        Port("submit", input=str, output="TurnResult"),      # run a turn
        Port("compact", input=None, output=bool),              # compact context
    ]

    # Ports this block requires (default behavior if absent)
    requires = [
        Port("llm_call", input=dict, output=dict),             # satisfied by a Provider block
        Port("get_tools", input=None, output=list),            # satisfied by a Registry block (optional)
    ]
```

**Design:**
- Each block declares `provides` (offered ports) and `requires` (needed ports)
- `requires` is optional — if unconnected, default behavior (graceful degradation)
- The Organism auto-matches ports when assembling blocks

---

## 3. Organism — the block assembler

The container that assembles all blocks into a single organism.

```python
from ludex import Organism
from ludex.blocks import EngineBlock, ProviderBlock, ResilienceBlock, TrackingBlock

# Create an organism — assemble only the blocks you need
organism = Organism(
    name="lxm-poker-agent",
    blocks=[
        ProviderBlock(provider="ollama", model="exaone3.5:7.8b"),
        ResilienceBlock(max_retries=3, backoff_base=2.0),
        EngineBlock(max_turns=100, token_budget=4000),
        TrackingBlock(output_dir="./results/exp5"),
    ]
)

# The Organism automatically:
# 1. Matches each block's requires/provides ports
# 2. Wires up Bus and Signals
# 3. Shares Config
# 4. Falls back to default behavior for missing requires

result = organism.run("Your hand is A♠ K♥. Will you bet?")
```

### Assembly process (internal)

```
1. Register blocks
   EngineBlock, ProviderBlock, ResilienceBlock, TrackingBlock

2. Match ports
   Engine.requires["llm_call"] ←→ Provider.provides["llm_call"]  ✓ matched
   Engine.requires["get_tools"] ←→ (none)                         → default behavior
   Resilience.requires["llm_call"] ←→ Provider.provides["llm_call"] ✓ wrapped

3. Wrapping chain (Resilience wraps Provider)
   Engine → Resilience.llm_call() → Provider.llm_call()
   (retry, backoff, circuit breaker applied automatically)

4. Wire Bus/Signals
   Every block shares the same Bus and Signals instance

5. Inject Config
   Every block references the same Config instance
```

### Adding/removing blocks (organ transplant)

```python
# Attach a Memory block later (organ transplant)
organism.attach(MemoryBlock(backend="lancedb"))
# → auto port-matching, Bus/Signals wiring

# Remove the Tracking block
organism.detach("tracking")
# → unsubscribe from Bus/Signals, disconnect ports
# → other blocks are unaffected (graceful degradation)
```

---

## 4. The Block base class

The base class every block inherits from.

```python
from dataclasses import dataclass, field
from typing import Any, Optional
from abc import ABC, abstractmethod

@dataclass
class Port:
    """A connection interface between blocks"""
    name: str
    input: Any = None
    output: Any = None
    required: bool = True  # False = optional

class Block(ABC):
    """Base class for every organ block"""

    provides: list[Port] = []
    requires: list[Port] = []

    def __init__(self):
        self._bus: Optional[Bus] = None
        self._signals: Optional[Signals] = None
        self._config: Optional[Config] = None
        self._connections: dict[str, Any] = {}

    # --- Lifecycle ---
    def attach(self, bus: Bus, signals: Signals, config: Config) -> None:
        """Called when attached to an organism. Injects Bus/Signals/Config."""
        self._bus = bus
        self._signals = signals
        self._config = config
        self.on_attach()

    def detach(self) -> None:
        """Called when detached from an organism. Cleanup."""
        self.on_detach()
        self._bus = None
        self._signals = None
        self._config = None
        self._connections.clear()

    # --- Override points ---
    def on_attach(self) -> None:
        """After the block is attached. Register signal subscriptions, etc."""
        pass

    def on_detach(self) -> None:
        """Before the block is detached. Cleanup."""
        pass

    # --- Port resolution ---
    def connect(self, port_name: str, target: Any) -> None:
        """Connect a requires port to another block's provides"""
        self._connections[port_name] = target

    def call_port(self, port_name: str, *args, **kwargs) -> Any:
        """Call a connected port. If unconnected, default behavior."""
        if port_name in self._connections:
            return self._connections[port_name](*args, **kwargs)
        return self._default_for_port(port_name, *args, **kwargs)

    def _default_for_port(self, port_name: str, *args, **kwargs) -> Any:
        """Default behavior when a port is unconnected. Override in subclasses."""
        raise NotImplementedError(f"Port '{port_name}' not connected and no default")
```

---

## 5. Nine block interfaces at a glance

| Block | provides | requires | Bus publishes | Signals subscribes |
|------|----------|----------|----------|-------------|
| **Engine** | `submit`, `compact` | `llm_call`, `get_tools` | `turn.result` | `config.changed` |
| **Provider** | `llm_call`, `health_check` | (none) | `llm.response` | `config.changed` |
| **Resilience** | `resilient_call` (wraps llm_call) | `llm_call` | `retry.attempted` | `llm.failed` |
| **Registry** | `get_tools`, `execute_tool` | (none) | `tool.executed` | `config.changed` |
| **Tracking** | `get_report`, `get_cost` | (none) | (none) | `turn.*`, `llm.*`, `error.*` |
| **Memory** | `recall`, `store` | `embed` (optional) | `memory.recalled` | `turn.ended`, `session.ended` |
| **Hooks** | `register_hook` | (none) | (none) | `turn.*`, `tool.*` |
| **Plugins** | `load_plugin`, `list_plugins` | (none) | `plugin.loaded` | `session.started` |
| **Tasks** | `create_task`, `get_status` | (none) | `task.completed` | `session.started` |

---

## 6. Presets (common block combinations)

```python
# ludex/presets.py

def game_agent(provider, model, **kwargs):
    """LxM game agent: Engine + Provider + Resilience + Tracking"""
    return Organism(
        blocks=[
            ProviderBlock(provider=provider, model=model),
            ResilienceBlock(**kwargs.get("resilience", {})),
            EngineBlock(**kwargs.get("engine", {})),
            TrackingBlock(**kwargs.get("tracking", {})),
        ]
    )

def measurement(provider, models, **kwargs):
    """MTI measurement: Engine + Provider + Tracking + Registry"""
    return Organism(
        blocks=[
            ProviderBlock(provider=provider, model=models[0]),
            EngineBlock(**kwargs.get("engine", {})),
            TrackingBlock(**kwargs.get("tracking", {})),
            RegistryBlock(**kwargs.get("registry", {})),
        ]
    )

def minimal(provider, model):
    """Minimal setup: Provider + Resilience"""
    return Organism(
        blocks=[
            ProviderBlock(provider=provider, model=model),
            ResilienceBlock(),
        ]
    )
```

---

## 7. File structure

```
ludex/
├── __init__.py              # from ludex import Organism, Block
├── core/
│   ├── __init__.py
│   ├── bus.py               # Bus (circulatory — data flow)
│   ├── signals.py           # Signals (nervous — event notification)
│   ├── config.py            # Config (endocrine — global settings)
│   ├── port.py              # Port (connective tissue — interface)
│   ├── block.py             # Block base class
│   └── organism.py          # Organism assembler
├── blocks/
│   ├── __init__.py
│   ├── engine.py            # Engine Block (session/context)
│   ├── provider.py          # Provider Block (LLM calls)
│   ├── resilience.py        # Resilience Block (retry/fallback)
│   ├── registry.py          # Registry Block (tool management)
│   ├── tracking.py          # Tracking Block (records/cost)
│   ├── memory.py            # Memory Block (vector search)
│   ├── hooks.py             # Hooks Block (lifecycle)
│   ├── plugins.py           # Plugins Block (extensions)
│   └── tasks.py             # Tasks Block (async work)
├── presets.py               # preset combinations
└── utils/
    ├── __init__.py
    ├── encoding.py          # cp949/utf-8 handling
    └── json_schema.py       # JSON Schema utilities
```

---

## 8. Homeostasis — stability and feedback loops

Beyond simply connecting blocks: a system that, like a living body, maintains
stable **vital signs** and self-regulates.

### 8.1 Vital signs — the organism's biosignals

Just as a human has blood pressure, heart rate, and temperature, a Ludex
organism has biosignals worth monitoring.

```python
@dataclass(frozen=True)
class VitalSigns:
    """Biosignals representing the organism's current state"""

    # Metabolic indicators
    tokens_per_turn: float          # tokens consumed per turn (metabolic rate)
    token_budget_remaining: float   # remaining token budget (blood sugar)
    context_utilization: float      # context-window usage 0.0~1.0 (blood oxygen)

    # Circulatory indicators
    active_sessions: int            # active session count (heart rate)
    error_rate: float               # error rate over the last N turns (inflammation)
    latency_ms: float               # average response time (blood flow speed)

    # Immune indicators
    consecutive_failures: int       # consecutive failures (immune stress)
    circuit_breaker_open: bool      # circuit breaker tripped (immune over-reaction)

    # Memory indicators
    memory_entries: int             # stored memories (hippocampal capacity)
    memory_recall_hit_rate: float   # recall hit rate (memory strength)
```

### 8.2 Feedback loops — self-regulation mechanisms

**Negative feedback** — stabilizing. Reduces deviation to return to normal.

```python
class HomeostasisController:
    """Automatic regulator that maintains the organism's homeostasis"""

    def __init__(self, organism: Organism):
        self.organism = organism
        self.setpoints = {
            "error_rate": 0.05,           # target: error rate ≤ 5%
            "context_utilization": 0.7,   # target: 70% context utilization
            "latency_ms": 5000,           # target: respond within 5s
            "tokens_per_turn": 500,       # target: 500 tokens per turn
        }

    def check_and_regulate(self, vitals: VitalSigns) -> list[Regulation]:
        """Check biosignals and issue regulation commands"""
        regulations = []

        # High error rate → switch model (negative feedback)
        if vitals.error_rate > self.setpoints["error_rate"] * 2:
            regulations.append(Regulation(
                type="negative_feedback",
                trigger="error_rate_high",
                action="switch_model",
                reason=f"Error rate {vitals.error_rate:.1%} exceeds 2x setpoint"
            ))

        # Context saturation → auto-compact (negative feedback)
        if vitals.context_utilization > 0.9:
            regulations.append(Regulation(
                type="negative_feedback",
                trigger="context_saturation",
                action="compact_context",
                reason=f"Context {vitals.context_utilization:.0%} near capacity"
            ))

        # Consecutive failures → circuit breaker (immune response)
        if vitals.consecutive_failures >= 5:
            regulations.append(Regulation(
                type="immune_response",
                trigger="consecutive_failures",
                action="open_circuit_breaker",
                reason=f"{vitals.consecutive_failures} consecutive failures"
            ))

        # Excess token consumption → budget regulation (negative feedback)
        if vitals.tokens_per_turn > self.setpoints["tokens_per_turn"] * 1.5:
            regulations.append(Regulation(
                type="negative_feedback",
                trigger="high_token_consumption",
                action="reduce_context_window",
                reason=f"Token consumption {vitals.tokens_per_turn:.0f}/turn exceeds budget"
            ))

        return regulations
```

**Positive feedback** — amplifying. Accelerates under certain conditions.

```python
        # Sustained success → release circuit breaker (recovery)
        if vitals.consecutive_failures == 0 and vitals.circuit_breaker_open:
            regulations.append(Regulation(
                type="positive_feedback",
                trigger="recovery_detected",
                action="close_circuit_breaker",
                reason="System recovered, re-enabling normal operation"
            ))

        # High recall accuracy → widen memory search (reinforced learning)
        if vitals.memory_recall_hit_rate > 0.8:
            regulations.append(Regulation(
                type="positive_feedback",
                trigger="high_recall_accuracy",
                action="expand_memory_search",
                reason="Memory recall effective, expanding search scope"
            ))
```

### 8.3 Vital signs monitor — the biosignal dashboard

```python
class VitalSignsMonitor:
    """Real-time biosignal monitoring. Like a heart-rate monitor."""

    def __init__(self, organism: Organism, check_interval_turns: int = 5):
        self.organism = organism
        self.controller = HomeostasisController(organism)
        self.history: list[VitalSigns] = []
        self.check_interval = check_interval_turns

        # Auto-check every N turns
        organism.signals.on("turn.ended", self._on_turn_ended)

    def _on_turn_ended(self, turn_number: int, **kwargs):
        vitals = self._measure()
        self.history.append(vitals)

        if turn_number % self.check_interval == 0:
            regulations = self.controller.check_and_regulate(vitals)
            for reg in regulations:
                self._apply_regulation(reg)

    def _measure(self) -> VitalSigns:
        """Measure current biosignals"""
        # Collect indicators from each block
        ...

    def _apply_regulation(self, reg: Regulation):
        """Execute a regulation command"""
        if reg.action == "switch_model":
            self.organism.config.set("model", self._get_fallback_model())
        elif reg.action == "compact_context":
            self.organism.blocks["engine"].compact()
        elif reg.action == "open_circuit_breaker":
            self.organism.blocks["resilience"].open_circuit()
        ...
```

---

## 9. Entity boundary

### 9.1 What is an entity?

One `Organism` instance = one entity. An entity's boundary is defined
differently depending on the execution environment.

```
┌─────────────────────────────────────────────────────┐
│                    Entity Boundary                   │
│                                                      │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐              │
│  │Engine│ │Provid│ │Resil.│ │Track.│  ... blocks   │
│  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘              │
│     │        │        │        │                    │
│  ===╪========╪========╪========╪=== Bus (internal)  │
│  ---┼--------┼--------┼--------┼--- Signals (int.)  │
│     │        │        │        │                    │
│  ┌──┴────────┴────────┴────────┴──┐                │
│  │    Homeostasis Controller      │                │
│  │    Vital Signs Monitor         │                │
│  └────────────────────────────────┘                │
│                                                      │
│  [Config]  [Identity]  [Membrane]                   │
│                                                      │
└─────────────────── Membrane ────────────────────────┘
         │                        ▲
         ▼                        │
   External World (other entities, APIs, users)
```

### 9.2 Boundary types

| Boundary type | Example | Isolation | Communication |
|----------|------|----------|----------|
| **Process** | Multiple Organisms in the same Python process | Low (memory may be shared) | Direct method calls |
| **Folder** | Different directories on the same machine | Medium (filesystem isolation) | File-based IPC |
| **Container** | Docker container | High (OS-level isolation) | HTTP/WebSocket |
| **Machine** | Different physical server | High (network isolation) | HTTP/gRPC + TLS |
| **Network** | Remote over the internet | Highest (firewall, NAT) | HTTPS + auth + encryption |

### 9.3 Membrane — the cell membrane (entity boundary control)

Like a biological cell membrane, it distinguishes inside from outside at the
entity boundary and controls passage.

```python
class Membrane:
    """An entity's cell membrane. Distinguishes internal/external traffic and applies security."""

    def __init__(self, entity_id: str, boundary_type: str):
        self.entity_id = entity_id
        self.boundary_type = boundary_type  # process, folder, container, machine, network
        self.allowed_peers: set[str] = set()  # list of permitted external entities
        self.exposed_ports: dict[str, Port] = {}  # ports exposed externally

    def expose(self, port_name: str, port: Port) -> None:
        """Expose an internal port to the outside (express a receptor)"""
        self.exposed_ports[port_name] = port

    def hide(self, port_name: str) -> None:
        """Hide a port from the outside"""
        self.exposed_ports.pop(port_name, None)

    def allow_peer(self, peer_entity_id: str) -> None:
        """Permit access from a specific external entity"""
        self.allowed_peers.add(peer_entity_id)

    def validate_incoming(self, source_entity_id: str, port_name: str, data: Any) -> bool:
        """Validate an incoming external request"""
        if source_entity_id not in self.allowed_peers:
            return False  # unpermitted entity → block
        if port_name not in self.exposed_ports:
            return False  # unexposed port → block
        return True

    def wrap_outgoing(self, target_entity_id: str, data: Any) -> WrappedMessage:
        """Add entity identification + signature to outgoing data"""
        return WrappedMessage(
            source=self.entity_id,
            target=target_entity_id,
            payload=data,
            signature=self._sign(data),
            boundary=self.boundary_type,
        )
```

### 9.4 Internal vs external communication

**Internal communication (within one entity):**
- Use Bus, Signals, Config directly
- Trust-based — no validation (same cell)
- Fast (in-memory)
- Every block can access all data

**External communication (between entities):**
- Only possible through the Membrane
- Always authenticated/validated (another cell must cross the membrane)
- Only exposed ports are accessible
- Protocol varies by boundary type

```python
class EntityBridge:
    """A communication bridge between entities"""

    def __init__(self, local: Organism, remote_membrane: MembranProxy):
        self.local = local
        self.remote = remote_membrane

    async def call_remote(self, port_name: str, data: Any) -> Any:
        """Call an exposed port on a remote entity"""
        # 1. Wrap (sign) the outgoing message at the local Membrane
        wrapped = self.local.membrane.wrap_outgoing(
            self.remote.entity_id, data
        )
        # 2. Transmit according to boundary type
        match self.remote.boundary_type:
            case "process":
                return self.remote.direct_call(port_name, wrapped)
            case "folder":
                return await self._file_ipc(port_name, wrapped)
            case "container" | "machine":
                return await self._http_call(port_name, wrapped)
            case "network":
                return await self._https_call(port_name, wrapped)
```

### 9.5 Multi-entity scenarios

```python
# Scenario 1: LxM poker — 4 entities in the same process
players = [
    Organism(name="player-1", blocks=[ProviderBlock("ollama", "exaone3.5:7.8b"), ...]),
    Organism(name="player-2", blocks=[ProviderBlock("ollama", "mistral:7b"), ...]),
    Organism(name="player-3", blocks=[ProviderBlock("ollama", "llama3.1:8b"), ...]),
    Organism(name="player-4", blocks=[ProviderBlock("claude", "haiku"), ...]),
]
# Boundary: process level. Each has independent Config/Bus/Signals.
# A game manager talks to each entity through the Membrane.

# Scenario 2: MTI measurement — per-model isolation
# Each model is its own Organism
# A measurement tool injects stimuli from outside via the Membrane → measures response

# Scenario 3: distributed agents — network boundary
# An Organism on server A cooperates with one on server B over HTTPS
# Each Membrane handles auth/encryption
```

---

## 10. Identity — entity identification

```python
@dataclass(frozen=True)
class EntityIdentity:
    """An entity's unique identity"""
    entity_id: str              # UUID — unique identifier
    name: str                   # human-readable name
    species: str                # classification (e.g., "game_agent", "measurement", "minimal")
    lineage: str                # lineage (e.g., "ludex.presets.game_agent")
    created_at: str             # birth time (ISO 8601)
    vital_signs_setpoints: dict # this entity's normal vital ranges
    membrane_policy: str        # "open", "selective", "closed"
```

---

## 11. Implementation order (roadmap)

### Completed phases

| Phase | Target | Status | Notes |
|------|----------|------|------|
| **Phase 0** | `core/` (Bus, Signals, Config, Port, Block, Organism) | ✅ done | 22 tests |
| **Phase 0.5** | VitalSigns, TimeAwareness, Homeostasis, Membrane, Identity | ✅ done | integrated into core |
| **Phase 1** | Provider (Ollama/OpenAI/Anthropic adapters) + Resilience | ✅ done | 12 tests |
| **Phase 1r** | Adapter-pattern refactor + Config single-source principle | ✅ done | found in retrospective |
| **Phase 2** | Engine (multi-turn) + Tracking (JSONL) | ✅ done | 13 tests |
| **Phase 3** | Registry + Hooks | ✅ done | 12 tests |
| **E2E** | 6 integration scenarios | ✅ done | 6 tests |
| **Live** | Real Ollama connection + Trust Game experiment | ✅ done | — |

### Next phases (in priority order)

| Phase | Target | Rationale |
|------|----------|------|
| **Phase 4a** | Emotion adapter | An adapter by which the Shell senses/responds to the Core's emotional state. Direct vectors for a Claude API brain; behavior-based estimation for an SLM. Adds EmotionalVitals to VitalSigns. |
| **Phase 4b** | Memory Block + Dream | Cross-session memory retention + an AutoDream pattern (memory cleanup/compaction). File-based memory first; vector search later. |
| **Phase 4c** | Immune strengthening | Self-repair/evolution mechanisms. Desperation detection → automatic intervention. Emotion-based immunity + adaptive immunity (learning from experience). |
| **Phase 5a** | Game Manager Organism | A separate organism manages a game. The start of the Gaia ecosystem. Inter-organism communication via EntityBridge. |
| **Phase 5b** | Multi-entity experiments | Various models + various games + multiple agents. |
| **Phase 6** | SLM emotion benchmark | Apply the emotion methodology to SLMs. "How vulnerable is this model to desperation?" A new use for MTI. |
| **Phase 7** | Emotion control package | Apply psychological (CBT) patterns. Administer "emotional stabilizers" from the Shell to the Core. Model therapeutics. |
| **Phase 8** | Plugins + Tasks | Manifest-based dynamic loading + async work + scheduling. |

---

## 12. Emotion research integration

### Background

Research on emotion concepts inside large language models has shown that
internal "emotion vectors" can be identified and that they causally drive
behavior. Ludex takes this as a finding about the *Core* (the brain). Since
Ludex is a *Shell* (body) project, the response is to build an adapter by which
the Shell senses and responds to the Core's emotional state.

**EmotionalVitals (a VitalSigns extension):**

```python
@dataclass(frozen=True)
class EmotionalVitals:
    valence: float           # positive/negative (-1 ~ +1)
    arousal: float           # arousal level (0 ~ 1)
    desperation: float       # desperation level (a misalignment-risk indicator)
    calm: float              # calmness level (a stability indicator)
    dominant_emotion: str    # the strongest emotion
    estimation_method: str   # "vector" (Claude API) or "behavioral" (SLM estimate)
```

**Emotion-estimation strategy (per provider):**
- Claude API: direct measurement if/when an internal-vector API becomes available (future)
- Ollama/SLM: estimate emotion from response-text analysis (behavior-based)
- All providers: analyze response emotional tone in the Hooks `after_turn`

**Homeostasis extension:**
- Desperation exceeds threshold → automatic intervention (model switch, context reset, calming-prompt injection)
- Check emotional state in Hooks `before_turn` → auto-inject calming context if needed

### The AutoDream pattern (see Memory Block)

AutoDream = cross-session memory cleanup (similar to REM sleep):
- Relative date → absolute date conversion
- Remove contradictory information
- Clean up stale memories
- Reorganize by topic

When implementing the Memory Block, realize this pattern as `compact()` +
`dream()` methods.

### "Biorhythm" observation

An observation during development: an agent saying "let's take a break" after a
long work session could be read as a functional emotion (a protective
mechanism).
