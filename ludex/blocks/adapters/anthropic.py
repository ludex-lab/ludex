"""
Anthropic Adapter — /v1/messages

Claude API has a different format from OpenAI:
- system is a top-level field, not a message
- content is an array of content blocks
- tool_use is a content block type

Reference: OC extensions/anthropic/, CC rust/crates/api/client.rs
"""

from __future__ import annotations

import json
from ludex.blocks.adapters.base import BaseAdapter, AdapterResponse


class AnthropicAdapter(BaseAdapter):
    """Anthropic Claude API 어댑터"""

    provider_name = "anthropic"
    API_VERSION = "2023-06-01"

    def __init__(self, base_url: str = "https://api.anthropic.com", api_key: str = "", **kwargs):
        super().__init__(base_url=base_url, api_key=api_key, **kwargs)

    def call(self, model, prompt="", system="", messages=None, temperature=0.7, max_tokens=4096, tools=None, effort=""):
        url = f"{self.base_url}/v1/messages"

        if messages:
            # 멀티턴: system 메시지를 분리 (Anthropic은 top-level system)
            api_messages = []
            extracted_system = system
            for msg in messages:
                if msg["role"] == "system":
                    extracted_system = msg["content"]
                else:
                    api_messages.append(msg)
        else:
            api_messages = [{"role": "user", "content": prompt}]
            extracted_system = system

        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": api_messages,
        }

        if extracted_system:
            body["system"] = extracted_system

        if temperature is not None:
            body["temperature"] = temperature

        if tools:
            # Anthropic tool format
            body["tools"] = tools

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
        }

        data = json.dumps(body).encode("utf-8")
        result = self._request(url, data=data, headers=headers, method="POST")

        # Parse Anthropic response format (content blocks)
        content_text = ""
        tool_calls = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                content_text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "arguments": block.get("input", {}),
                })

        usage = result.get("usage", {})

        return AdapterResponse(
            content=content_text,
            tokens_in=usage.get("input_tokens", 0),
            tokens_out=usage.get("output_tokens", 0),
            tool_calls=tool_calls,
            raw=result,
        )

    def health_check(self):
        # Anthropic doesn't have a models endpoint, just check auth
        try:
            url = f"{self.base_url}/v1/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": self.API_VERSION,
            }
            # Send minimal request to check auth
            body = {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            }
            data = json.dumps(body).encode("utf-8")
            self._request(url, data=data, headers=headers, method="POST")
            return {"status": "healthy", "provider": "anthropic"}
        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg:
                return {"status": "unhealthy", "provider": "anthropic", "error": "Invalid API key"}
            return {"status": "unhealthy", "provider": "anthropic", "error": error_msg}

    # Fallback when /v1/models is unreachable (no key, network). Kept as a
    # last resort only — the live endpoint is authoritative for currency.
    _KNOWN_MODELS = [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ]

    def list_models(self):
        try:
            url = f"{self.base_url}/v1/models"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": self.API_VERSION,
            }
            result = self._request(url, headers=headers)
            models = [m["id"] for m in result.get("data", []) if m.get("id")]
            return models or list(self._KNOWN_MODELS)
        except Exception:
            return list(self._KNOWN_MODELS)
