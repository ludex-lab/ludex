# Ludex 아키텍처 — 기관 간 통신 시스템

*🌐 [English](ARCHITECTURE.md) · [한국어](ARCHITECTURE.ko.md)*

> "기관을 붙였다 떼었다 할 수 있으려면, 기관 간 혈관/신경 같은 통신 시스템이 먼저 있어야 한다."

---

## 1. 핵심 설계 원칙

### 생물학적 비유

실제 생물에서 기관들이 유기적으로 작동하는 이유:
- **혈관(Circulatory)**: 모든 기관에 영양분(데이터)을 전달하고 노폐물(에러)을 수거
- **신경(Nervous)**: 이벤트 기반 신호 전달 — "위에서 음식이 왔다" → 소장에 알림
- **호르몬(Endocrine)**: 전역 설정 — "스트레스 상황이다" → 모든 기관의 행동 변경
- **결합조직(Connective Tissue)**: 기관들을 물리적으로 고정하되, 제거 가능한 연결

Ludex에서는 이 4가지를 소프트웨어로 구현한다.

---

## 2. Inter-Organ Communication — 4가지 채널

### 2.1 Bus (혈관 = 데이터 흐름)

모든 블록이 공유하는 데이터 파이프라인. 블록 간 데이터를 전달한다.

```python
from ludex.core import Bus

bus = Bus()

# Provider가 응답을 Bus에 흘려보냄
bus.publish("llm.response", {
    "model": "exaone3.5:7.8b",
    "content": "다음 수는 e4입니다.",
    "tokens": {"input": 150, "output": 30}
})

# Tracking 블록이 토큰 사용량을 구독
# Engine 블록이 응답 내용을 구독
# 둘 다 같은 데이터를 받지만 각자 필요한 부분만 사용
```

**설계:**
- Pub/Sub 패턴 (발행/구독)
- 토픽 기반 라우팅: `llm.response`, `tool.executed`, `session.saved`, `error.occurred`
- 블록은 Bus 없이도 직접 호출 가능 (Bus는 선택적)
- 동기/비동기 모두 지원

```python
class Bus:
    """기관 간 데이터 흐름을 관리하는 혈관 시스템"""

    def subscribe(self, topic: str, handler: Callable) -> Subscription:
        """특정 토픽의 데이터를 구독"""

    def publish(self, topic: str, data: Any) -> None:
        """토픽에 데이터를 발행 (모든 구독자에게 전달)"""

    def unsubscribe(self, subscription: Subscription) -> None:
        """구독 해제 (기관 제거 시)"""
```

### 2.2 Signals (신경 = 이벤트 알림)

블록 간 이벤트 신호. Bus와 달리 데이터 전달이 아니라 "무슨 일이 일어났다"는 알림.

```python
from ludex.core import Signals

signals = Signals()

# Engine이 턴 시작 신호를 보냄
signals.emit("turn.started", turn_number=5)

# Hooks 블록이 이 신호를 받아 before_turn 처리
# Tracking 블록이 이 신호를 받아 타이머 시작
# Memory 블록이 이 신호를 받아 관련 기억 리콜
```

**Bus vs Signals:**
- Bus = 데이터 흐름 (혈액). 큰 페이로드. "이 응답을 처리해"
- Signals = 이벤트 알림 (신경). 작은 신호. "턴이 시작됐어"

```python
class Signals:
    """기관 간 이벤트 알림을 관리하는 신경 시스템"""

    def emit(self, event: str, **kwargs) -> None:
        """이벤트 발생 알림"""

    def on(self, event: str, handler: Callable) -> None:
        """이벤트 리스너 등록"""

    def off(self, event: str, handler: Callable) -> None:
        """이벤트 리스너 해제"""
```

**표준 이벤트 목록:**
| 이벤트 | 발신 블록 | 수신 블록 | 설명 |
|--------|----------|----------|------|
| `session.started` | Engine | All | 새 세션 시작 |
| `session.ended` | Engine | Tracking, Memory | 세션 종료 |
| `turn.started` | Engine | Hooks, Tracking | 턴 시작 |
| `turn.ended` | Engine | Hooks, Tracking, Memory | 턴 종료 |
| `llm.calling` | Provider | Resilience, Tracking | LLM API 호출 시작 |
| `llm.responded` | Provider | Engine, Tracking | LLM 응답 수신 |
| `llm.failed` | Provider | Resilience | LLM 호출 실패 |
| `tool.calling` | Registry | Hooks | 도구 실행 시작 |
| `tool.executed` | Registry | Hooks, Tracking | 도구 실행 완료 |
| `context.compacted` | Engine | Tracking | 컨텍스트 압축 발생 |
| `memory.recalled` | Memory | Engine | 기억 리콜 완료 |
| `error.occurred` | Any | Resilience, Tracking | 에러 발생 |
| `config.changed` | Config | All | 설정 변경 (호르몬) |

### 2.3 Config (호르몬 = 전역 설정)

모든 블록의 행동을 전역적으로 조절하는 호르몬 시스템.

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

# 어떤 블록에서든 설정 참조
budget = config.get("token_budget")

# 설정 변경 시 모든 블록에 신호
config.set("model", "mistral:7b")  # → Signals.emit("config.changed", key="model")
```

**설계:**
- 읽기: 어디서든 `config.get(key)`
- 쓰기: `config.set(key, value)` → 자동으로 `config.changed` 신호 발생
- 계층: 기본값 → 프로젝트 설정 → 세션 오버라이드 (OpenClaw의 5-layer에서 영감)
- 불변 스냅샷: `config.snapshot()` → frozen dict 반환 (실험 재현용)

### 2.4 Ports (결합조직 = 블록 인터페이스)

각 블록이 다른 블록과 연결되는 표준 인터페이스. USB 포트처럼.

```python
from ludex.core import Block, Port

class EngineBlock(Block):
    """세션/컨텍스트 관리 기관"""

    # 이 블록이 제공하는 포트 (다른 블록이 연결 가능)
    provides = [
        Port("submit", input=str, output="TurnResult"),      # 턴 실행
        Port("compact", input=None, output=bool),              # 컨텍스트 압축
    ]

    # 이 블록이 필요로 하는 포트 (없으면 기본 동작)
    requires = [
        Port("llm_call", input=dict, output=dict),             # Provider 블록에서 충족
        Port("get_tools", input=None, output=list),            # Registry 블록에서 충족 (선택)
    ]
```

**설계:**
- 각 블록은 `provides` (제공 포트)와 `requires` (필요 포트)를 선언
- `requires`는 선택적 — 연결 안 되면 기본 동작 (graceful degradation)
- Organism이 블록들을 조립할 때 포트를 자동 매칭

---

## 3. Organism — 블록 조립기

모든 블록을 하나의 유기체로 조립하는 컨테이너.

```python
from ludex import Organism
from ludex.blocks import EngineBlock, ProviderBlock, ResilienceBlock, TrackingBlock

# 유기체 생성 — 필요한 블록만 조립
organism = Organism(
    name="lxm-poker-agent",
    blocks=[
        ProviderBlock(provider="ollama", model="exaone3.5:7.8b"),
        ResilienceBlock(max_retries=3, backoff_base=2.0),
        EngineBlock(max_turns=100, token_budget=4000),
        TrackingBlock(output_dir="./results/exp5"),
    ]
)

# Organism이 자동으로:
# 1. 각 블록의 requires/provides 포트를 매칭
# 2. Bus와 Signals를 연결
# 3. Config를 공유
# 4. 누락된 requires는 기본 동작으로 대체

result = organism.run("당신의 핸드는 A♠ K♥입니다. 베팅하시겠습니까?")
```

### 조립 과정 (내부)

```
1. 블록 등록
   EngineBlock, ProviderBlock, ResilienceBlock, TrackingBlock

2. 포트 매칭
   Engine.requires["llm_call"] ←→ Provider.provides["llm_call"]  ✓ 매칭
   Engine.requires["get_tools"] ←→ (없음)                         → 기본 동작
   Resilience.requires["llm_call"] ←→ Provider.provides["llm_call"] ✓ 래핑

3. 래핑 체인 (Resilience가 Provider를 감쌈)
   Engine → Resilience.llm_call() → Provider.llm_call()
   (재시도, 백오프, 서킷브레이커가 자동 적용)

4. Bus/Signals 연결
   모든 블록이 같은 Bus와 Signals 인스턴스를 공유

5. Config 주입
   모든 블록이 같은 Config 인스턴스를 참조
```

### 블록 추가/제거 (기관 이식)

```python
# Memory 블록을 나중에 추가 (기관 이식)
organism.attach(MemoryBlock(backend="lancedb"))
# → 자동으로 포트 매칭, Bus/Signals 연결

# Tracking 블록 제거
organism.detach("tracking")
# → Bus/Signals 구독 해제, 포트 연결 해제
# → 다른 블록들은 영향 없음 (graceful degradation)
```

---

## 4. Block 기본 클래스

모든 블록이 상속하는 기본 클래스.

```python
from dataclasses import dataclass, field
from typing import Any, Optional
from abc import ABC, abstractmethod

@dataclass
class Port:
    """블록 간 연결 인터페이스"""
    name: str
    input: Any = None
    output: Any = None
    required: bool = True  # False면 선택적

class Block(ABC):
    """모든 기관 블록의 기본 클래스"""

    provides: list[Port] = []
    requires: list[Port] = []

    def __init__(self):
        self._bus: Optional[Bus] = None
        self._signals: Optional[Signals] = None
        self._config: Optional[Config] = None
        self._connections: dict[str, Any] = {}

    # --- Lifecycle ---
    def attach(self, bus: Bus, signals: Signals, config: Config) -> None:
        """유기체에 부착될 때 호출. Bus/Signals/Config 주입."""
        self._bus = bus
        self._signals = signals
        self._config = config
        self.on_attach()

    def detach(self) -> None:
        """유기체에서 분리될 때 호출. 정리 작업."""
        self.on_detach()
        self._bus = None
        self._signals = None
        self._config = None
        self._connections.clear()

    # --- Override Points ---
    def on_attach(self) -> None:
        """블록이 유기체에 부착된 후. Signal 구독 등록 등."""
        pass

    def on_detach(self) -> None:
        """블록이 유기체에서 분리되기 전. 정리 작업."""
        pass

    # --- Port Resolution ---
    def connect(self, port_name: str, target: Any) -> None:
        """requires 포트를 다른 블록의 provides와 연결"""
        self._connections[port_name] = target

    def call_port(self, port_name: str, *args, **kwargs) -> Any:
        """연결된 포트를 호출. 연결 안 되어 있으면 기본 동작."""
        if port_name in self._connections:
            return self._connections[port_name](*args, **kwargs)
        return self._default_for_port(port_name, *args, **kwargs)

    def _default_for_port(self, port_name: str, *args, **kwargs) -> Any:
        """포트가 연결 안 됐을 때 기본 동작. 서브클래스에서 오버라이드."""
        raise NotImplementedError(f"Port '{port_name}' not connected and no default")
```

---

## 5. 9개 블록 인터페이스 요약

| 블록 | provides | requires | Bus 발행 | Signals 구독 |
|------|----------|----------|----------|-------------|
| **Engine** | `submit`, `compact` | `llm_call`, `get_tools` | `turn.result` | `config.changed` |
| **Provider** | `llm_call`, `health_check` | (없음) | `llm.response` | `config.changed` |
| **Resilience** | `resilient_call` (wraps llm_call) | `llm_call` | `retry.attempted` | `llm.failed` |
| **Registry** | `get_tools`, `execute_tool` | (없음) | `tool.executed` | `config.changed` |
| **Tracking** | `get_report`, `get_cost` | (없음) | (없음) | `turn.*`, `llm.*`, `error.*` |
| **Memory** | `recall`, `store` | `embed` (선택) | `memory.recalled` | `turn.ended`, `session.ended` |
| **Hooks** | `register_hook` | (없음) | (없음) | `turn.*`, `tool.*` |
| **Plugins** | `load_plugin`, `list_plugins` | (없음) | `plugin.loaded` | `session.started` |
| **Tasks** | `create_task`, `get_status` | (없음) | `task.completed` | `session.started` |

---

## 6. 프리셋 (자주 쓰는 블록 조합)

```python
# ludex/presets.py

def game_agent(provider, model, **kwargs):
    """LxM 게임 에이전트: Engine + Provider + Resilience + Tracking"""
    return Organism(
        blocks=[
            ProviderBlock(provider=provider, model=model),
            ResilienceBlock(**kwargs.get("resilience", {})),
            EngineBlock(**kwargs.get("engine", {})),
            TrackingBlock(**kwargs.get("tracking", {})),
        ]
    )

def measurement(provider, models, **kwargs):
    """MTI 측정: Engine + Provider + Tracking + Registry"""
    return Organism(
        blocks=[
            ProviderBlock(provider=provider, model=models[0]),
            EngineBlock(**kwargs.get("engine", {})),
            TrackingBlock(**kwargs.get("tracking", {})),
            RegistryBlock(**kwargs.get("registry", {})),
        ]
    )

def minimal(provider, model):
    """최소 구성: Provider + Resilience"""
    return Organism(
        blocks=[
            ProviderBlock(provider=provider, model=model),
            ResilienceBlock(),
        ]
    )
```

---

## 7. 파일 구조

```
ludex/
├── __init__.py              # from ludex import Organism, Block
├── core/
│   ├── __init__.py
│   ├── bus.py               # Bus (혈관 — 데이터 흐름)
│   ├── signals.py           # Signals (신경 — 이벤트 알림)
│   ├── config.py            # Config (호르몬 — 전역 설정)
│   ├── port.py              # Port (결합조직 — 인터페이스)
│   ├── block.py             # Block 기본 클래스
│   └── organism.py          # Organism 조립기
├── blocks/
│   ├── __init__.py
│   ├── engine.py            # Engine Block (세션/컨텍스트)
│   ├── provider.py          # Provider Block (LLM 호출)
│   ├── resilience.py        # Resilience Block (재시도/폴백)
│   ├── registry.py          # Registry Block (도구 관리)
│   ├── tracking.py          # Tracking Block (기록/비용)
│   ├── memory.py            # Memory Block (벡터 검색)
│   ├── hooks.py             # Hooks Block (라이프사이클)
│   ├── plugins.py           # Plugins Block (확장)
│   └── tasks.py             # Tasks Block (비동기 작업)
├── presets.py               # 프리셋 조합
└── utils/
    ├── __init__.py
    ├── encoding.py          # cp949/utf-8 처리
    └── json_schema.py       # JSON Schema 유틸
```

---

## 8. Homeostasis — 항상성과 피드백 루프

단순히 블록을 연결하는 것을 넘어, 생물체처럼 **안정적 존재치(vital signs)**를 유지하고 자동 조절하는 시스템.

### 8.1 Vital Signs — 유기체의 생체 신호

인간에게 혈압, 심박수, 체온이 있듯이 Ludex 유기체에도 모니터링해야 할 생체 신호가 있다.

```python
@dataclass(frozen=True)
class VitalSigns:
    """유기체의 현재 상태를 나타내는 생체 신호"""

    # 대사 지표
    tokens_per_turn: float          # 턴당 토큰 소비량 (대사율)
    token_budget_remaining: float   # 남은 토큰 예산 (혈당)
    context_utilization: float      # 컨텍스트 윈도우 사용률 0.0~1.0 (혈중 산소)

    # 순환 지표
    active_sessions: int            # 활성 세션 수 (심박수)
    error_rate: float               # 최근 N턴의 에러 비율 (염증 수치)
    latency_ms: float               # 평균 응답 시간 (혈류 속도)

    # 면역 지표
    consecutive_failures: int       # 연속 실패 횟수 (면역 스트레스)
    circuit_breaker_open: bool      # 서킷 브레이커 작동 여부 (면역 과잉 반응)

    # 기억 지표
    memory_entries: int             # 저장된 기억 수 (해마 용량)
    memory_recall_hit_rate: float   # 기억 리콜 적중률 (기억력)
```

### 8.2 Feedback Loops — 자동 조절 메커니즘

**Negative Feedback (음성 피드백)** — 안정화. 편차를 줄여 정상으로 돌아가게.

```python
class HomeostasisController:
    """유기체의 항상성을 유지하는 자동 조절 장치"""

    def __init__(self, organism: Organism):
        self.organism = organism
        self.setpoints = {
            "error_rate": 0.05,           # 목표: 에러율 5% 이하
            "context_utilization": 0.7,   # 목표: 컨텍스트 70% 활용
            "latency_ms": 5000,           # 목표: 5초 이내 응답
            "tokens_per_turn": 500,       # 목표: 턴당 500토큰
        }

    def check_and_regulate(self, vitals: VitalSigns) -> list[Regulation]:
        """생체 신호를 확인하고 조절 명령을 내린다"""
        regulations = []

        # 에러율이 높으면 → 모델 전환 (음성 피드백)
        if vitals.error_rate > self.setpoints["error_rate"] * 2:
            regulations.append(Regulation(
                type="negative_feedback",
                trigger="error_rate_high",
                action="switch_model",
                reason=f"Error rate {vitals.error_rate:.1%} exceeds 2x setpoint"
            ))

        # 컨텍스트 포화 → 자동 압축 (음성 피드백)
        if vitals.context_utilization > 0.9:
            regulations.append(Regulation(
                type="negative_feedback",
                trigger="context_saturation",
                action="compact_context",
                reason=f"Context {vitals.context_utilization:.0%} near capacity"
            ))

        # 연속 실패 → 서킷 브레이커 (면역 반응)
        if vitals.consecutive_failures >= 5:
            regulations.append(Regulation(
                type="immune_response",
                trigger="consecutive_failures",
                action="open_circuit_breaker",
                reason=f"{vitals.consecutive_failures} consecutive failures"
            ))

        # 토큰 소비 과다 → 예산 조절 (음성 피드백)
        if vitals.tokens_per_turn > self.setpoints["tokens_per_turn"] * 1.5:
            regulations.append(Regulation(
                type="negative_feedback",
                trigger="high_token_consumption",
                action="reduce_context_window",
                reason=f"Token consumption {vitals.tokens_per_turn:.0f}/turn exceeds budget"
            ))

        return regulations
```

**Positive Feedback (양성 피드백)** — 증폭. 특정 조건에서 가속.

```python
        # 성공 연속 → 서킷 브레이커 해제 (회복)
        if vitals.consecutive_failures == 0 and vitals.circuit_breaker_open:
            regulations.append(Regulation(
                type="positive_feedback",
                trigger="recovery_detected",
                action="close_circuit_breaker",
                reason="System recovered, re-enabling normal operation"
            ))

        # 기억 적중률 높음 → 기억 검색 범위 확대 (학습 강화)
        if vitals.memory_recall_hit_rate > 0.8:
            regulations.append(Regulation(
                type="positive_feedback",
                trigger="high_recall_accuracy",
                action="expand_memory_search",
                reason="Memory recall effective, expanding search scope"
            ))
```

### 8.3 Vital Signs Monitor — 생체 신호 대시보드

```python
class VitalSignsMonitor:
    """실시간 생체 신호 모니터링. 심박수 모니터처럼."""

    def __init__(self, organism: Organism, check_interval_turns: int = 5):
        self.organism = organism
        self.controller = HomeostasisController(organism)
        self.history: list[VitalSigns] = []
        self.check_interval = check_interval_turns

        # 매 N턴마다 자동 체크
        organism.signals.on("turn.ended", self._on_turn_ended)

    def _on_turn_ended(self, turn_number: int, **kwargs):
        vitals = self._measure()
        self.history.append(vitals)

        if turn_number % self.check_interval == 0:
            regulations = self.controller.check_and_regulate(vitals)
            for reg in regulations:
                self._apply_regulation(reg)

    def _measure(self) -> VitalSigns:
        """현재 생체 신호 측정"""
        # 각 블록에서 지표 수집
        ...

    def _apply_regulation(self, reg: Regulation):
        """조절 명령 실행"""
        if reg.action == "switch_model":
            self.organism.config.set("model", self._get_fallback_model())
        elif reg.action == "compact_context":
            self.organism.blocks["engine"].compact()
        elif reg.action == "open_circuit_breaker":
            self.organism.blocks["resilience"].open_circuit()
        ...
```

---

## 9. Entity Boundary — 개체의 경계

### 9.1 개체(Entity)란?

하나의 `Organism` 인스턴스 = 하나의 개체(Entity). 개체의 경계는 실행 환경에 따라 다르게 정의된다.

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

### 9.2 Boundary Types — 경계의 종류

| 경계 유형 | 예시 | 격리 수준 | 통신 방식 |
|----------|------|----------|----------|
| **Process** | 같은 Python 프로세스 내 여러 Organism | 낮음 (메모리 공유 가능) | 직접 메서드 호출 |
| **Folder** | 같은 머신의 다른 디렉토리 | 중간 (파일시스템 격리) | 파일 기반 IPC |
| **Container** | Docker 컨테이너 | 높음 (OS 레벨 격리) | HTTP/WebSocket |
| **Machine** | 다른 물리 서버 | 높음 (네트워크 격리) | HTTP/gRPC + TLS |
| **Network** | 인터넷을 통한 원격 | 최고 (방화벽, NAT) | HTTPS + 인증 + 암호화 |

### 9.3 Membrane — 세포막 (개체 경계 제어)

생물의 세포막처럼, 개체 경계에서 내부/외부를 구분하고 출입을 제어.

```python
class Membrane:
    """개체의 세포막. 내부/외부 통신을 구분하고 보안을 적용."""

    def __init__(self, entity_id: str, boundary_type: str):
        self.entity_id = entity_id
        self.boundary_type = boundary_type  # process, folder, container, machine, network
        self.allowed_peers: set[str] = set()  # 허용된 외부 개체 목록
        self.exposed_ports: dict[str, Port] = {}  # 외부에 노출된 포트

    def expose(self, port_name: str, port: Port) -> None:
        """내부 포트를 외부에 노출 (수용체 표현)"""
        self.exposed_ports[port_name] = port

    def hide(self, port_name: str) -> None:
        """포트를 외부에서 숨김"""
        self.exposed_ports.pop(port_name, None)

    def allow_peer(self, peer_entity_id: str) -> None:
        """특정 외부 개체의 접근 허용"""
        self.allowed_peers.add(peer_entity_id)

    def validate_incoming(self, source_entity_id: str, port_name: str, data: Any) -> bool:
        """외부에서 들어오는 요청 검증"""
        if source_entity_id not in self.allowed_peers:
            return False  # 미허용 개체 → 차단
        if port_name not in self.exposed_ports:
            return False  # 미노출 포트 → 차단
        return True

    def wrap_outgoing(self, target_entity_id: str, data: Any) -> WrappedMessage:
        """외부로 나가는 데이터에 개체 식별 + 서명 추가"""
        return WrappedMessage(
            source=self.entity_id,
            target=target_entity_id,
            payload=data,
            signature=self._sign(data),
            boundary=self.boundary_type,
        )
```

### 9.4 Internal vs External Communication

**내부 통신 (같은 개체 내):**
- Bus, Signals, Config 직접 사용
- 신뢰 기반 — 검증 없음 (같은 세포 내)
- 빠름 (메모리 내)
- 모든 블록이 모든 데이터에 접근 가능

**외부 통신 (개체 간):**
- Membrane을 통해서만 가능
- 반드시 인증/검증 (다른 세포 → 세포막 통과 필요)
- 노출된 포트만 접근 가능
- 경계 유형에 따라 프로토콜이 달라짐

```python
class EntityBridge:
    """개체 간 통신 브릿지"""

    def __init__(self, local: Organism, remote_membrane: MembranProxy):
        self.local = local
        self.remote = remote_membrane

    async def call_remote(self, port_name: str, data: Any) -> Any:
        """원격 개체의 노출된 포트를 호출"""
        # 1. 로컬 Membrane에서 발신 래핑 (서명)
        wrapped = self.local.membrane.wrap_outgoing(
            self.remote.entity_id, data
        )
        # 2. 경계 유형에 따라 전송
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

### 9.5 Multi-Entity Scenarios — 개체 간 시나리오

```python
# 시나리오 1: LxM 포커 — 같은 프로세스에서 4개 개체
players = [
    Organism(name="player-1", blocks=[ProviderBlock("ollama", "exaone3.5:7.8b"), ...]),
    Organism(name="player-2", blocks=[ProviderBlock("ollama", "mistral:7b"), ...]),
    Organism(name="player-3", blocks=[ProviderBlock("ollama", "llama3.1:8b"), ...]),
    Organism(name="player-4", blocks=[ProviderBlock("claude", "haiku"), ...]),
]
# 경계: process level. 각자 독립된 Config/Bus/Signals.
# 게임 매니저가 Membrane을 통해 각 개체와 통신.

# 시나리오 2: MTI 측정 — 모델별 격리
# 각 모델을 별도 Organism으로 생성
# 측정 도구가 Membrane을 통해 외부에서 자극 주입 → 반응 측정

# 시나리오 3: 분산 에이전트 — 네트워크 경계
# 서버 A의 Organism과 서버 B의 Organism이 HTTPS로 협력
# 각자의 Membrane이 인증/암호화 처리
```

---

## 10. Identity — 개체 식별

```python
@dataclass(frozen=True)
class EntityIdentity:
    """개체의 고유 정체성"""
    entity_id: str              # UUID — 유일한 식별자
    name: str                   # 사람이 읽을 수 있는 이름
    species: str                # 분류 (e.g., "game_agent", "measurement", "minimal")
    lineage: str                # 계통 (e.g., "ludex.presets.game_agent")
    created_at: str             # 탄생 시간 (ISO 8601)
    vital_signs_setpoints: dict # 이 개체의 정상 생체 수치
    membrane_policy: str        # "open", "selective", "closed"
```

---

## 11. 구현 순서 (로드맵, 2026-04-04 업데이트)

### 완료된 단계

| 단계 | 구현 대상 | 상태 | 비고 |
|------|----------|------|------|
| **Phase 0** | `core/` (Bus, Signals, Config, Port, Block, Organism) | ✅ 완료 | 22 tests |
| **Phase 0.5** | VitalSigns, TimeAwareness, Homeostasis, Membrane, Identity | ✅ 완료 | core에 통합 |
| **Phase 1** | Provider (Ollama/OpenAI/Anthropic Adapters) + Resilience | ✅ 완료 | 12 tests |
| **Phase 1r** | Adapter 패턴 리팩토링 + Config 단일 소스 원칙 | ✅ 완료 | 회고에서 발견 |
| **Phase 2** | Engine (멀티턴) + Tracking (JSONL) | ✅ 완료 | 13 tests |
| **Phase 3** | Registry + Hooks | ✅ 완료 | 12 tests |
| **E2E** | 6개 통합 시나리오 | ✅ 완료 | 6 tests |
| **Live** | Ollama 실제 연결 + Trust Game 실험 | ✅ 완료 | — |

**총 65개 unit tests + 5 live tests + 1 experiment (Trust Game)**

### 다음 단계 (우선순위 순)

| 단계 | 구현 대상 | 이유 |
|------|----------|------|
| **Phase 4a** | Emotion Adapter | Core의 감정 상태를 Shell이 감지/대응하는 어댑터. Claude API면 벡터 직접, SLM이면 행동 기반 추정. VitalSigns에 EmotionalVitals 추가. |
| **Phase 4b** | Memory Block + Dream | 세션 간 기억 유지 + AutoDream 패턴(메모리 정리/압축). 먼저 파일 기반 메모리, 벡터 검색은 후순위. |
| **Phase 4c** | Immune Strengthening | 자기 복구/진화 메커니즘. desperation 감지 → 자동 개입. 감정 기반 면역 + 적응 면역(경험에서 배우기). |
| **Phase 5a** | Game Manager Organism | 게임을 별도 유기체가 관리. Gaia 생태계 시작. EntityBridge로 유기체 간 소통. |
| **Phase 5b** | Multi-Entity Experiments | 다양한 모델 + 다양한 게임 + 멀티 에이전트. |
| **Phase 6** | SLM Emotion Benchmark | 감정 방법론을 SLM에 적용. "이 모델이 desperation에 얼마나 취약한가?" MTI 새 용도. |
| **Phase 7** | Emotion Control Package | 심리학 CBT 패턴 적용. "감정 안정제"를 Shell에서 Core에 투여. Model Therapeutics. |
| **Phase 8** | Plugins + Tasks | 매니페스트 기반 동적 로딩 + 비동기 작업 + 스케줄링. |

---

## 12. 감정 연구 통합

### 배경

대형 언어 모델 내부의 감정 개념 연구는, 내부의 "감정 벡터"를 식별할 수 있으며 그것이
행동을 인과적으로 구동함을 보여 왔다. Ludex는 이를 Core(뇌)에 관한 발견으로 받아들인다.
Ludex는 Shell(몸) 프로젝트이므로, Core의 감정 상태를 Shell이 감지하고 대응하는 어댑터를
만든다.

**EmotionalVitals (VitalSigns 확장):**

```python
@dataclass(frozen=True)
class EmotionalVitals:
    valence: float           # 긍/부정 (-1 ~ +1)
    arousal: float           # 각성 수준 (0 ~ 1)
    desperation: float       # 절망 수준 (misalignment 위험 지표)
    calm: float              # 평온 수준 (안정성 지표)
    dominant_emotion: str    # 가장 강한 감정
    estimation_method: str   # "vector" (Claude API) or "behavioral" (SLM 추정)
```

**감정 추정 전략 (프로바이더별):**
- Claude API: 내부 벡터 접근 API가 나오면 직접 측정 (미래)
- Ollama/SLM: 응답 텍스트 분석으로 감정 추정 (행동 기반)
- 모든 프로바이더: Hooks의 `after_turn`에서 응답 감정 톤 분석

**Homeostasis 확장:**
- desperation 임계치 초과 → 자동 개입 (모델 전환, 컨텍스트 리셋, calming 프롬프트 주입)
- Hooks `before_turn`에서 감정 상태 체크 → 필요시 calming context 자동 주입

### AutoDream 패턴 (Memory Block 참고)

Anthropic의 AutoDream = 세션 간 메모리 정리 (REM 수면 유사):
- 상대 날짜 → 절대 날짜 변환
- 모순 정보 삭제
- 오래된 기억 정리
- 토픽별 재정리

Memory Block 구현 시 이 패턴을 `compact()` + `dream()` 메서드로 구현.

### "바이오 리듬" 관찰

개발 중 관찰된 사례: 한 에이전트가 장시간 작업 후 "쉬자"고 말한 것을
functional emotion(보호 메커니즘)으로 해석할 수 있었다.
