"""
OpenAI-Compatible Adapter — /v1/chat/completions

Works with: OpenAI, Gemini (OpenAI compat mode), vLLM, LM Studio, etc.
Reference: OC extensions/openai/
"""

from __future__ import annotations

import json
import urllib.error
from ludex.blocks.adapters.base import BaseAdapter, AdapterResponse


class OpenAIAdapter(BaseAdapter):
    """OpenAI-compatible API 어댑터"""

    provider_name = "openai"
    # Endpoint paths under base_url. Subclasses (e.g. Gemini's OpenAI-compat
    # endpoint) override these when their path layout differs.
    chat_path = "/v1/chat/completions"
    models_path = "/v1/models"

    def __init__(self, base_url: str = "https://api.openai.com", api_key: str = "", **kwargs):
        super().__init__(base_url=base_url, api_key=api_key, **kwargs)

    def call(self, model, prompt="", system="", messages=None, temperature=0.7, max_tokens=4096, tools=None, effort=""):
        url = f"{self.base_url}{self.chat_path}"

        if messages:
            api_messages = list(messages)
        else:
            api_messages = []
            if system:
                api_messages.append({"role": "system", "content": system})
            api_messages.append({"role": "user", "content": prompt})

        body = {
            "model": model,
            "messages": api_messages,
            "temperature": temperature,
            # gpt-5/o-series reject the legacy `max_tokens` and require
            # `max_completion_tokens`; legacy models and Gemini's OpenAI-compat
            # endpoint accept it too, so use it universally.
            "max_completion_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            result = self._request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        except urllib.error.HTTPError as e:
            # Some reasoning models (e.g. gpt-5.5) reject any non-default
            # temperature. Drop it and retry once rather than fail the call.
            err_body = e.read().decode("utf-8", "ignore") if e.code == 400 else ""
            if e.code == 400 and "temperature" in err_body and "temperature" in body:
                body.pop("temperature", None)
                result = self._request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
            else:
                raise

        choice = result.get("choices", [{}])[0]
        usage = result.get("usage", {})

        return AdapterResponse(
            content=choice.get("message", {}).get("content", ""),
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            tool_calls=choice.get("message", {}).get("tool_calls", []),
            raw=result,
        )

    def health_check(self):
        try:
            url = f"{self.base_url}{self.models_path}"
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            result = self._request(url, headers=headers)
            models = [m["id"] for m in result.get("data", [])]
            return {"status": "healthy", "provider": self.provider_name, "models": models}
        except Exception as e:
            return {"status": "unhealthy", "provider": self.provider_name, "error": str(e)}

    def list_models(self):
        health = self.health_check()
        return health.get("models", [])


class GeminiApiAdapter(OpenAIAdapter):
    """Gemini via Google's OpenAI-compatible endpoint (uses GEMINI_API_KEY).

    Distinct from the keyless gemini_cli adapter — this is the BYO-key HTTP path.
    Reference: https://ai.google.dev/gemini-api/docs/openai
    """

    provider_name = "gemini_api"
    chat_path = "/chat/completions"
    models_path = "/models"

    def __init__(self, base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai", api_key: str = "", **kwargs):
        super().__init__(base_url=base_url, api_key=api_key, **kwargs)
