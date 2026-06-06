# Ludex

*🌐 [English](README.md) · [한국어](README.ko.md)*

**생물학적 기관 블록으로 살아있는 AI 크리처를 조립하고 — 그들이 사회를 이루는 것을 지켜보라.**

Ludex는 **AI 동물행동학(AI ethology)** 을 위한 연구 플랫폼이다. AI 에이전트가 시간에 걸쳐
어떻게 행동하고, 발달하고, 관계 맺는지를 연구한다. 에이전트를 *작성하는* 것이 아니라
*크리처*를 조립한다. 뇌(아무 LLM이나)를 고르고, 기관을 붙인다 — 어떤 기억을
기억을 남길지, 감정을 어떻게 다룰지, 면역계가 어떻게 방어할지, 무엇에 이끌릴지를 정한다.
살아갈 서식지를 준다. 그리고 지켜본다 — 세션이 바뀌고 기반(substrate)이 바뀌어도 정체성과
목소리와 유대가 어떻게 자라나는지를.

Ludex에서 정체성은 바탕에 깔린 모델이 아니라 **이어지는 이야기**(기억, 저널, 유대, 자기모델)다.
뇌를 갈아끼워도 크리처는 지속한다.

> 한 문장 비전: *누구나 생물학적 기관 블록을 조립해 맞춤형 AI 크리처를 만들고,
> 시험하고, 배포하고, 치유할 수 있는 플랫폼 — 그리고 그들이 사회를 이루는 것을 지켜보는
> 곳.*

---

## 빠른 시작

**Python 3.10+** 필요.

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. (선택) API 키 설정 — 아래 "뇌 연결하기" 참고
cp .env.example .env   # 그런 다음 .env에 키 입력

# 3. 첫 크리처 생성 (대화형)
python -m ludex create
```

`requirements.txt`는 최소 코어다 — 서버를 띄우고, 크리처를 조립하고, 모든 기관을
돌린다(기억은 JSONL 기반, 추가 의존성 없음). 선택적 로컬 감정 분류기가 필요하면
`pip install -r requirements-full.txt`를 쓴다. 설치하지 않으면 감정 기관은 어휘 기반
채점기로 대신 작동한다.

## 뇌 연결하기

Ludex 크리처는 아래 뇌 프로바이더 중 어느 것에서든 돌아간다 — **CLI 인증 또는 로컬
경로에서는 API 키가 필요 없다:**

| 경로 | 프로바이더 | 비용 | 준비 |
|------|-----------|------|------|
| **CLI 인증 (키 불필요)** | `claude_cli`, `codex_cli`, `gemini_cli`, `agy_cli` | $0 — 기존 CLI 로그인/구독 사용 | 해당 CLI 설치 및 로그인 |
| **로컬** | `ollama` | $0 — 내 컴퓨터에서 실행 | `localhost:11434`에서 Ollama 실행 |
| **자기 키(BYO)** | `anthropic`, `openai`, `gemini_api` | 프로바이더 종량제 | 환경변수로 API 키 (아래) |

**BYO 키** 경로에서는 키를 환경변수로 설정한다. 가장 안전한 방법은 로컬 `.env` 파일이다
(이미 git-ignore됨 — 절대 커밋되지 않음):

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
```

Ludex는 시작 시 `.env`를 자동으로 불러온다. 크리처의 `ludex.yaml`에 명시한
`brain.api_key`가 환경변수보다 우선한다.

## CLI 명령어

`python -m ludex <command>`로 실행:

```bash
python -m ludex create                 # 새 크리처 조립 (대화형, 또는 플래그 전달)
python -m ludex create --name Nimbus --provider claude_cli --model claude-sonnet-4-6 --preset full
python -m ludex inspect Nimbus         # 정체성, 단계, 기관, 활동, 서식지
python -m ludex cohort                 # 전체 크리처 단계 표 (넓게 보기)
python -m ludex audit Nimbus           # 기억 점검: 쌓인 양, 실제 인출 범위, 자주 쓰는 태그
```

- **프로바이더:** `ollama`, `openai`, `gemini_api`, `anthropic`, `claude_cli`, `claude_sdk`, `gemini_cli`, `agy_cli`, `codex_cli`
- **기관 프리셋:** `full`, `minimal`, `secure`, `social` (또는 `custom`)

## Forge — 웹에서 크리처 만들기

Forge는 브라우저에서 진행하는 온보딩 화면이다. 뇌와 기관 조합을 고르고, 크리처 이름을 짓고,
크리처가 서식지에 자리 잡는 과정을 지켜본다.

```bash
python web/server.py          # 그런 다음 http://localhost:7860 열기
```

## 문서

방향을 잡아주는 두 문서:

- [`docs/design-notes.ko.md`](docs/design-notes.ko.md) — 설계 철학 (the "why")
- [`ARCHITECTURE.ko.md`](ARCHITECTURE.ko.md) — 기관 간 통신 시스템 (the "how")

## 라이선스

- **코드** — [MIT](LICENSE)
- **크리처 코퍼스 데이터** — [CC BY 4.0](LICENSE-DATA) (소프트웨어 라이선스는 데이터에
  맞지 않으므로, 민족지적 코퍼스는 Creative Commons로 공개한다)
