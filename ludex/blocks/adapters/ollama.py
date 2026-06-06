"""
Ollama Adapter — /api/chat

Reference: OC extensions/ollama/src/stream.ts, defaults.ts
"""

from __future__ import annotations

import json
from ludex.blocks.adapters.base import BaseAdapter, AdapterResponse


class OllamaAdapter(BaseAdapter):
    """Ollama API 어댑터. http://127.0.0.1:11434/api/chat"""

    provider_name = "ollama"

    def __init__(self, base_url: str = "http://127.0.0.1:11434",
                 timeout_ms: int = 120000, **kwargs):
        super().__init__(base_url=base_url, timeout_ms=timeout_ms, **kwargs)

    def call(self, model, prompt="", system="", messages=None, temperature=0.7, max_tokens=4096, tools=None, effort=""):
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

        body = {
            "model": model,
            "messages": api_messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            # Disable thinking for faster response (thinking models like gemma4)
            "think": False,
        }
        if tools:
            body["tools"] = tools

        data = json.dumps(body).encode("utf-8")
        result = self._request(url, data=data, method="POST")

        return AdapterResponse(
            content=result.get("message", {}).get("content", ""),
            tokens_in=result.get("prompt_eval_count", 0),
            tokens_out=result.get("eval_count", 0),
            tool_calls=result.get("message", {}).get("tool_calls", []),
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

    def supports_tools(self, model: str) -> bool:
        """Probe whether this model accepts the tools parameter.

        Some Ollama models (gemma family, exaone, deepseek-r1) reject
        function-calling. This probe sends a tiny call with tools and
        checks for the 'does not support tools' error.
        """
        url = f"{self.base_url}/api/chat"
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {
                "name": "probe", "description": "probe",
                "parameters": {"type": "object", "properties": {}}
            }}],
            "stream": False,
        }).encode("utf-8")
        try:
            self._request(url, data=body, method="POST")
            return True
        except Exception as e:
            msg = str(e)
            if "does not support tools" in msg:
                return False
            # Other errors -- be conservative, assume unsupported
            return False
