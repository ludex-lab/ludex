"""
Claude Agent SDK Adapter -- uses claude_agent_sdk.query() instead of subprocess.

STATUS: Experimental.
- Direct usage (CLI scripts) works.
- Use from inside FastAPI server is blocked by an anyio + Windows + nested-threading
  issue: SDK control-request initialize times out at ~60s when called from a worker
  thread spawned by asyncio.to_thread(). The same code works fine when invoked from
  the main thread of a standalone script.
- Investigation pending. Possible fixes: dedicated worker process, ProcessPoolExecutor,
  or making the FastAPI endpoint synchronous (sync def + manual asyncio.run).

For now, the production path for Claude in FORGE is `claude_cli` (subprocess with
--system-prompt flag). This SDK adapter is kept for direct-script usage and as the
target for MCP integration we want eventually.

Advantages when working:
- Real system_prompt parameter (not text injection)
- Native cwd parameter (CLAUDE.md auto-discovery in habitat)
- Native MCP server integration (Ludex organs auto-registered)
- Streaming + structured event handling
- Tool call events parsed properly

Requires claude_agent_sdk:
    pip install claude-agent-sdk

Windows requires CLAUDE_CODE_GIT_BASH_PATH set in env (auto-detected if Git is at D:\\Git).

Reference: https://github.com/anthropics/claude-agent-sdk-python
"""

from __future__ import annotations

import os
import time
import asyncio
import logging
from typing import Any

from ludex.blocks.adapters.base import BaseAdapter, AdapterResponse

logger = logging.getLogger(__name__)


# Windows-friendly default
_DEFAULT_CLI_PATH = (
    r"C:\Users\jihoo\AppData\Roaming\npm\claude.cmd"
    if os.name == "nt" and os.path.exists(r"C:\Users\jihoo\AppData\Roaming\npm\claude.cmd")
    else None
)


class ClaudeSdkAdapter(BaseAdapter):
    """Claude Code via Agent SDK."""

    provider_name = "claude_sdk"

    def __init__(
        self,
        base_url: str = "",
        timeout_ms: int = 180000,
        cwd: str = "",
        cli_path: str = "",
        env_extra: dict | None = None,
        mcp_servers: dict | None = None,
        allowed_tools: list | None = None,
        **kwargs,
    ):
        super().__init__(base_url=base_url or "claude_sdk", timeout_ms=timeout_ms, **kwargs)
        self._cwd = cwd or None
        self._cli_path = cli_path or _DEFAULT_CLI_PATH
        self._env_extra = env_extra or {}
        # On Windows, claude_agent_sdk requires the bash path
        if os.name == "nt" and "CLAUDE_CODE_GIT_BASH_PATH" not in self._env_extra:
            for candidate in [r"D:\Git\bin\bash.exe", r"C:\Program Files\Git\bin\bash.exe"]:
                if os.path.exists(candidate):
                    self._env_extra["CLAUDE_CODE_GIT_BASH_PATH"] = candidate
                    break
        self._mcp_servers = mcp_servers or {}
        self._allowed_tools = allowed_tools or []

    def call(
        self,
        model: str = "",
        prompt: str = "",
        system: str = "",
        messages: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list | None = None,
        effort: str = "",
    ) -> AdapterResponse:
        try:
            from claude_agent_sdk import query, ClaudeAgentOptions
        except ImportError:
            return AdapterResponse(
                content="[Error: claude-agent-sdk not installed. pip install claude-agent-sdk]",
                raw={"error": "import_error"},
            )

        # Extract system prompt from messages if present
        system_prompt = system or ""
        user_parts = []
        if messages:
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    system_prompt = content if not system_prompt else system_prompt + "\n\n" + content
                elif role == "assistant":
                    user_parts.append(f"[Previous turn — assistant]: {content}")
                elif role == "tool":
                    user_parts.append(f"[Tool result]: {content}")
                else:
                    user_parts.append(content)
            full_prompt = "\n\n".join(user_parts)
        else:
            full_prompt = prompt

        if not full_prompt:
            full_prompt = "(no message)"

        # Build options
        opts_kwargs = {
            "max_turns": 5,
        }
        if system_prompt:
            opts_kwargs["system_prompt"] = system_prompt
        if self._cwd:
            opts_kwargs["cwd"] = self._cwd
            opts_kwargs["add_dirs"] = [self._cwd]
        if self._cli_path:
            opts_kwargs["cli_path"] = self._cli_path
        if self._env_extra:
            opts_kwargs["env"] = self._env_extra
        if self._mcp_servers:
            opts_kwargs["mcp_servers"] = self._mcp_servers
            # Auto-allow all ludex tools if MCP server is registered
            if self._mcp_servers and not self._allowed_tools:
                opts_kwargs["allowed_tools"] = ["mcp__ludex__*"]
        if self._allowed_tools:
            opts_kwargs["allowed_tools"] = self._allowed_tools
        if model and model != "claude-code":
            opts_kwargs["model"] = model

        options = ClaudeAgentOptions(**opts_kwargs)

        async def run_query() -> tuple[str, list, dict]:
            content_parts = []
            tool_calls = []
            usage = {}
            try:
                async for msg in query(prompt=full_prompt, options=options):
                    msg_type = type(msg).__name__
                    if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                        for block in msg.content:
                            if hasattr(block, "text") and block.text:
                                content_parts.append(block.text)
                            elif hasattr(block, "name"):
                                tool_calls.append({
                                    "id": getattr(block, "id", ""),
                                    "function": {
                                        "name": block.name,
                                        "arguments": getattr(block, "input", {}),
                                    },
                                })
                    elif msg_type == "ResultMessage":
                        if hasattr(msg, "result"):
                            # Final result string
                            if msg.result and not content_parts:
                                content_parts.append(str(msg.result))
                        if hasattr(msg, "usage"):
                            usage = msg.usage if isinstance(msg.usage, dict) else {}
            except Exception as e:
                logger.exception("Claude SDK query failed")
                content_parts.append(f"[SDK error: {e}]")
            return ("\n".join(content_parts), tool_calls, usage)

        start = time.time()
        # Always run in a dedicated thread with its own fresh event loop.
        # The Claude SDK uses anyio + subprocess which needs proper loop init.
        # Even if we're already in a thread (FastAPI's asyncio.to_thread), spawning
        # another thread is the safest way to get a clean asyncio.run() context.
        import threading
        result_holder: dict = {}

        def worker():
            try:
                result_holder["result"] = asyncio.run(run_query())
            except Exception as e:
                logger.exception("Claude SDK worker thread failed")
                result_holder["error"] = e

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=self.timeout_ms / 1000)

        if "error" in result_holder:
            return AdapterResponse(
                content=f"[Adapter error: {result_holder['error']}]",
                raw={"error": str(result_holder['error'])},
            )
        if "result" not in result_holder:
            return AdapterResponse(
                content="[Adapter timeout]",
                raw={"error": "timeout", "elapsed_ms": (time.time() - start) * 1000},
            )
        content, tcs, usage = result_holder["result"]

        elapsed_ms = (time.time() - start) * 1000
        return AdapterResponse(
            content=content or "(no response)",
            tokens_in=usage.get("input_tokens", len(full_prompt) // 4),
            tokens_out=usage.get("output_tokens", len(content) // 4),
            tool_calls=tcs,
            raw={
                "elapsed_ms": round(elapsed_ms, 1),
                "usage": usage,
                "cwd": self._cwd,
            },
        )

    def health_check(self) -> dict:
        try:
            from claude_agent_sdk import query, ClaudeAgentOptions
            return {
                "status": "ok",
                "provider": "claude_sdk",
                "cwd": self._cwd or "(inherit)",
                "cli_path": self._cli_path or "(default)",
            }
        except ImportError as e:
            return {"status": "error", "provider": "claude_sdk", "error": f"claude-agent-sdk not installed: {e}"}
        except Exception as e:
            return {"status": "error", "provider": "claude_sdk", "error": str(e)}

    def list_models(self) -> list[str]:
        return ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5"]
