"""
Ollama Adapter — /api/chat

Reference: OC extensions/ollama/src/stream.ts, defaults.ts
"""

from __future__ import annotations

import json
import logging

from ludex.blocks.adapters.base import BaseAdapter, AdapterResponse

logger = logging.getLogger(__name__)

# Thinking tiers a model's template actually accepts, by model-name prefix.
# Ollama's `think` takes false, true, or a level string, and the levels are
# not the same everywhere: qwen3.8's template serves low/medium/xhigh and has
# no "high", while Ludex's common effort scale does. A level the template does
# not know is a bad wire value, and this project has already paid twice for
# effort reaching a CLI that rejects it — both times as an empty response,
# which is the silent shape we keep buying (see effort_contract's note).
#
# Reported by 이음 (ludex-village) 2026-08-26 from measurement on an M3 Ultra:
# with think=false a 4B model answered a permutation problem in 0.17s and got
# it wrong; with think="low" it spent the entire 1,000-token output budget on
# reasoning and returned an empty final answer. Both failures are real and
# neither is visible without wiring effort through in the first place.
_THINK_TIERS = {
    "qwen3.8": {"low", "medium", "xhigh"},
    "qwen3.5": {"low", "medium", "xhigh"},
}


def think_value(model: str, effort: str) -> bool | str:
    """`think` for this (model, effort) — False when there is nothing to send.

    Empty effort keeps the historical non-thinking behaviour exactly: a
    creature that never asked to think must not start thinking because this
    function appeared. An effort the model's template does not serve is
    dropped with a warning rather than sent — loudly, because a silent
    downgrade would make an unsupported tier look like a supported one.
    """
    if not effort:
        return False
    tiers = next((t for prefix, t in _THINK_TIERS.items()
                  if model.startswith(prefix)), None)
    if tiers is None:
        return effort           # unknown model: the server is the authority
    if effort in tiers:
        return effort
    logger.warning(
        "ollama: model %s does not serve think=%r (serves: %s) — sending "
        "think=false. Pin the creature's brain.effort to a served tier.",
        model, effort, ", ".join(sorted(tiers)))
    return False


class OllamaAdapter(BaseAdapter):
    """Ollama API 어댑터. http://127.0.0.1:11434/api/chat"""

    provider_name = "ollama"

    def __init__(self, base_url: str = "http://127.0.0.1:11434",
                 timeout_ms: int = 120000, **kwargs):
        super().__init__(base_url=base_url, timeout_ms=timeout_ms, **kwargs)

    def call(self, model, prompt="", system="", messages=None, temperature=0.7,
             max_tokens=4096, tools=None, effort="", num_ctx=None):
        url = f"{self.base_url}/api/chat"

        if messages:
            # 멀티턴: 전달받은 메시지 리스트 사용
            api_messages = list(messages)
        else:
            # 단일 턴: prompt를 user 메시지로
            api_messages = []
            if system:
                api_messages.append({"role": "system", "content": system})
            api_messages.append({"role": "user", "content": prompt})

        options = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        # KV cache size is a habitat cost, not a model detail: on the studio
        # host the same 4B model loads at 4.2 GB with num_ctx=32768 and at
        # 12 GB with the server default of 262144 — 7.8 GB of difference from
        # a field nobody could set (이음, 2026-08-26). Unset stays unset, so a
        # creature born before this line behaves exactly as it did; new ollama
        # births carry an explicit value (see DEFAULT_OLLAMA_NUM_CTX).
        if num_ctx:
            options["num_ctx"] = int(num_ctx)

        body = {
            "model": model,
            "messages": api_messages,
            # Stream so a long local generation has an observable heartbeat
            # and partial text/thinking can be retained if the wall deadline
            # fires. BaseAdapter aggregates the NDJSON back into this adapter's
            # historical single-response shape.
            "stream": True,
            "options": options,
            # Thinking follows the creature's own effort. Empty effort keeps
            # the historical non-thinking default.
            "think": think_value(model, effort),
        }
        if tools:
            body["tools"] = tools

        data = json.dumps(body).encode("utf-8")
        result = self._request(url, data=data, method="POST")

        msg = result.get("message", {}) or {}
        content = msg.get("content", "")
        # The reasoning trace stays OUT of content and lives in raw — a
        # thinking model must not have its scratch work read as its answer.
        # But an empty answer WITH a trace is its own failure and must not
        # look like a dead brain: the budget went to reasoning and none was
        # left for the reply. Named here so the turn above can tell the two
        # apart instead of seeing one silent empty string.
        thinking = msg.get("thinking") or ""
        tool_calls = msg.get("tool_calls", [])
        # Empty text is expected when the assistant's response is a function
        # call. The result-facing answer arrives on the next hop, so warning
        # that the reasoning budget ate the answer is false in this case.
        if not content.strip() and thinking.strip() and not tool_calls:
            if result.get("timeout"):
                logger.warning(
                    "ollama: %s timed out with partial reasoning but no final "
                    "answer (elapsed_ms=%s, thinking=%d chars). Partial work "
                    "is preserved in raw and must not be published as final.",
                    model, result.get("elapsed_ms"), len(thinking))
            else:
                logger.warning(
                    "ollama: %s spent its output budget on reasoning and returned "
                    "no answer (num_predict=%s, thinking=%d chars). Raise "
                    "max_tokens or lower brain.effort.",
                    model, max_tokens, len(thinking))

        return AdapterResponse(
            content=content,
            tokens_in=result.get("prompt_eval_count", 0),
            tokens_out=result.get("eval_count", 0),
            tool_calls=tool_calls,
            raw=result,
        )

    def health_check(self):
        try:
            result = self._request(f"{self.base_url}/api/tags")
            models = [m["name"] for m in result.get("models", [])]
            return {"status": "healthy", "provider": "ollama", "models": models}
        except Exception as e:
            return {"status": "unhealthy", "provider": "ollama", "error": str(e)}

    def list_models(self):
        health = self.health_check()
        return health.get("models", [])

    def supports_tools(self, model: str, num_ctx=None) -> bool:
        """Probe whether this model accepts the tools parameter.

        Some Ollama models (gemma family, exaone, deepseek-r1) reject
        function-calling. This probe sends a tiny call with tools and
        checks for the 'does not support tools' error.

        It builds its own request body, which is exactly how it kept its own
        blind spot: call() was taught num_ctx on 2026-08-26 and this was not,
        so a creature's FIRST contact with its brain — the FC wiring probe at
        build time — could still load the model at the server's 262144 default
        before any configured call arrived. 이음 measured it the same day:
        supports_tools("qwen3.5:4b") alone left `ollama ps` reading 12 GB /
        context 262144, where the fixed call path loads 4.2 GB / 32768.

        `think` is pinned false: this probe asks whether a capability exists,
        and paying for reasoning to answer that is waste — and a thinking
        model can spend the whole budget and return nothing, which this
        function would read as a failure.
        """
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {
                "name": "probe", "description": "probe",
                "parameters": {"type": "object", "properties": {}}
            }}],
            "stream": False,
            "think": False,
        }
        if num_ctx:
            payload["options"] = {"num_ctx": int(num_ctx)}
        body = json.dumps(payload).encode("utf-8")
        try:
            self._request(url, data=body, method="POST")
            return True
        except Exception as e:
            msg = str(e)
            if "does not support tools" in msg:
                return False
            # Other errors -- be conservative, assume unsupported
            return False
