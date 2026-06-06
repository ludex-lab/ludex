"""D-062 Phases 1 + 2a — same-machine MCP loopback adapter.

Proves that a conversation field can drive a creature through an
MCP-shaped transport instead of holding a direct organism reference.

- Phase 1 (`LocalMCPClient`): in-process tool dispatcher, no
  subprocess or socket. The call shape matches MCP exactly but the
  wire never exists.
- Phase 2a (`SubprocessMCPClient`): spawns
  `python -m ludex.mcp.ludex_mcp_server --habitat <path> --enable-engine`
  and talks via real stdio MCP protocol. Same machine, real wire —
  proves the process boundary before Phase 2b introduces networking.

Both clients expose the same `call_tool(name, args) -> ToolResult`
surface, so the shared `make_mcp_response_fn(client)` factory returns
a `ConversationField.ResponseFn` that is transport-agnostic.

Design rationale:

- The shape of a call through this adapter matches the MCP tool-call
  shape exactly: a tool name and a JSON-serialisable arguments dict
  round-trip to a content envelope. What the field holds is a
  `ResponseFn` (the `ConversationField.ResponseFn` signature from
  `ludex.fields.conversation`); it has no way to introspect the
  organism.
- Per-organism dispatchers avoid the `_organism` global in
  `ludex_mcp_server.py`. Multiple creatures (e.g. Hearth + Flint in
  the same field) can coexist with their own clients in the same
  process.
- Spans: every reach session emits a paired
  `reach_extended` / `reach_retracted` so ethnography can see the
  pipe lifecycle without inferring it from the absence of a direct
  call.

See `docs/cross-habitat-reach-design.md` §7 Phase 1 for the phasing
contract this file satisfies.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from ludex.core import trace as _trace

logger = logging.getLogger(__name__)


# Tool names — currently only one matters for reach, but the surface
# is intentionally string-keyed so a Phase 2 client that talks to a
# richer MCP surface can reuse the same dispatcher shape.
TOOL_ENGINE_SUBMIT = "ludex_engine_submit"


@dataclass(frozen=True)
class ToolResult:
    """MCP-shaped tool result envelope.

    Mirrors the `{"content": [{"type": "text", "text": ...}], "isError": bool}`
    shape the Ludex MCP server returns, but as a typed object so the
    adapter layer can assert on it without dict-digging.
    """
    text: str
    is_error: bool = False
    error: str = ""


class MCPClient(Protocol):
    """Minimal duck-type that both `LocalMCPClient` (Phase 1 in-process)
    and `SubprocessMCPClient` (Phase 2a stdio) satisfy. The factory
    `make_mcp_response_fn` depends only on this surface, which lets
    Phase 2b (TCP/WS/relay) land another client class without
    touching the ResponseFn layer."""

    organism_name: str

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> ToolResult: ...

    def close(self) -> None: ...


class LocalMCPClient:
    """Per-organism in-process tool dispatcher with MCP-shaped calls.

    Not the full MCP protocol — no initialize/listTools handshake,
    no session state — but the `call_tool(name, args) -> ToolResult`
    surface matches what a real MCP client would expose. Phase 2 can
    swap this for a stdio/TCP subclass and the adapter's
    `make_local_mcp_response_fn` keeps working.
    """

    def __init__(self, organism: Any):
        if organism is None:
            raise ValueError("LocalMCPClient requires a non-None organism")
        self._organism = organism
        self.organism_name = getattr(organism, "name", "unknown")

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        args = dict(arguments or {})
        if name == TOOL_ENGINE_SUBMIT:
            return self._dispatch_engine_submit(args)
        return ToolResult(
            text="",
            is_error=True,
            error=f"Unknown tool: {name}",
        )

    def _dispatch_engine_submit(self, args: dict[str, Any]) -> ToolResult:
        engine = self._organism.get_block("engine")
        if engine is None:
            return ToolResult(
                text="",
                is_error=True,
                error="Engine block not installed.",
            )
        prompt = args.get("prompt", "")
        system = args.get("system", "")
        try:
            result = engine.handle_submit(prompt, system=system)
        except Exception as e:
            logger.debug("engine.handle_submit raised", exc_info=True)
            return ToolResult(
                text="",
                is_error=True,
                error=f"engine.handle_submit raised: {e}",
            )
        response = getattr(result, "response", None) or ""
        return ToolResult(text=response)

    def close(self) -> None:
        """No-op for LocalMCPClient — nothing to tear down. Present so
        MCPClient Protocol conformance is trivial."""
        return None


# ----------------------------------------------------------------------
# Phase 2a — SubprocessMCPClient (real stdio MCP, same machine)
# ----------------------------------------------------------------------

class SubprocessMCPClient:
    """Spawns a `ludex_mcp_server` subprocess and talks stdio MCP.

    The subprocess is long-lived for the duration of a reach session —
    aligns with the "pipe is a relationship, not an RPC" commitment in
    `docs/cross-habitat-reach-design.md` §2. A background thread owns
    an asyncio event loop that holds the `stdio_client` +
    `ClientSession` context managers; sync `call_tool()` from field
    threads submits coroutines to that loop via
    `run_coroutine_threadsafe`.

    Same-machine, real process boundary — this is the Phase 2a proof
    that Phase 1's in-process dispatcher was just the shape. Phase 2b
    will add TCP/WS transport without touching this class's public
    surface.
    """

    # Sentinel matching the CLI flag on the server side. Changing the
    # flag name requires updating both places.
    _ENGINE_FLAG = "--enable-engine"

    def __init__(
        self,
        *,
        habitat_dir: str,
        organism_name: str = "",
        python_executable: str | None = None,
        extra_server_args: list[str] | None = None,
        env: Mapping[str, str] | None = None,
        startup_timeout_s: float = 30.0,
        call_timeout_s: float = 120.0,
    ):
        if not habitat_dir:
            raise ValueError("SubprocessMCPClient requires a habitat_dir")
        self._habitat_dir = os.path.abspath(habitat_dir)
        self.organism_name = organism_name or os.path.basename(self._habitat_dir)
        self._python = python_executable or sys.executable
        self._extra = list(extra_server_args or [])
        self._env = dict(env) if env is not None else None
        self._startup_timeout = float(startup_timeout_s)
        self._call_timeout = float(call_timeout_s)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._session = None  # mcp.ClientSession once initialized
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._closed = False

    # --- lifecycle -------------------------------------------------

    def start(self) -> None:
        """Spawn the subprocess and initialize the MCP session.
        Blocks until the server reports capabilities or `startup_timeout_s`
        elapses. Raises on startup failure."""
        if self._thread is not None:
            return  # already started
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"SubprocessMCPClient<{self.organism_name}>",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=self._startup_timeout):
            self.close()
            raise TimeoutError(
                f"SubprocessMCPClient startup timed out after "
                f"{self._startup_timeout}s (habitat={self._habitat_dir})"
            )
        if self._startup_error is not None:
            err = self._startup_error
            self.close()
            raise err

    def _server_argv(self) -> list[str]:
        return [
            self._python,
            "-m", "ludex.mcp.ludex_mcp_server",
            "--habitat", self._habitat_dir,
            self._ENGINE_FLAG,
            *self._extra,
        ]

    def _thread_main(self) -> None:
        try:
            # Windows note: Python 3.8+ default is ProactorEventLoop for
            # the main thread only. In worker threads we must create a
            # fresh loop explicitly.
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._async_setup())
                if self._startup_error is None:
                    self._loop.run_forever()
            finally:
                try:
                    self._loop.run_until_complete(self._async_teardown())
                except Exception:
                    logger.debug("async_teardown raised", exc_info=True)
                self._loop.close()
        except BaseException as e:
            # Last-resort capture so start() doesn't hang.
            logger.exception("SubprocessMCPClient thread crashed")
            self._startup_error = e
            self._ready.set()

    async def _async_setup(self) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            argv = self._server_argv()
            params = StdioServerParameters(
                command=argv[0],
                args=argv[1:],
                env=self._env,
                cwd=None,
            )
            self._exit_stack = AsyncExitStack()
            read, write = await self._exit_stack.enter_async_context(
                stdio_client(params)
            )
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
        except BaseException as e:
            self._startup_error = e
        finally:
            self._ready.set()

    async def _async_teardown(self) -> None:
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception:
                logger.debug("exit_stack.aclose raised", exc_info=True)
        self._exit_stack = None
        self._session = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = self._loop
        thread = self._thread
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=10.0)

    def __enter__(self) -> "SubprocessMCPClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- tool call -------------------------------------------------

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        if self._session is None or self._loop is None:
            return ToolResult(
                text="", is_error=True,
                error="SubprocessMCPClient is not started.",
            )
        args = dict(arguments or {})
        coro = self._session.call_tool(name, args)
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
            mcp_result = fut.result(timeout=self._call_timeout)
        except Exception as e:
            logger.debug("call_tool marshal failed", exc_info=True)
            return ToolResult(
                text="", is_error=True,
                error=f"MCP call_tool raised: {e}",
            )
        return _tool_result_from_mcp(mcp_result)


def _tool_result_from_mcp(result) -> ToolResult:
    """Marshal an `mcp.types.CallToolResult` into our typed shape.

    MCP's result has `content: list[Content]` and `isError: bool`.
    Content items may be TextContent, ImageContent, etc. — for
    `ludex_engine_submit` the server only emits TextContent, so we
    join `.text` across text items and drop the rest with a debug log.
    """
    is_error = bool(getattr(result, "isError", False))
    chunks: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            chunks.append(str(text))
        else:
            logger.debug("non-text MCP content item dropped: %r", item)
    joined = "".join(chunks)
    # MCP does not transmit a structured error string alongside
    # isError; the convention is to place the message in the text
    # content with isError=true. Preserve that convention here.
    err = joined if is_error else ""
    return ToolResult(
        text="" if is_error else joined,
        is_error=is_error,
        error=err,
    )


# ----------------------------------------------------------------------
# ResponseFn factory — the field-facing surface
# ----------------------------------------------------------------------

def make_mcp_response_fn(
    client: MCPClient,
    *,
    organism: Any = None,
    pipe_kind: str = "local_loopback",
    transport: str = "in_process",
    field_name: str | None = None,
) -> Callable[[Any, str], str]:
    """Return a `ConversationField.ResponseFn` that routes through
    the given MCP client. Transport-agnostic — works with
    `LocalMCPClient` (Phase 1 in-process), `SubprocessMCPClient`
    (Phase 2a stdio), or any future Phase 2b network client.

    `organism` is used for span emission (`emit_reach_*` writes to
    `organism`'s store). When absent (e.g. `SubprocessMCPClient`
    where the organism lives in the subprocess), pass `organism=None`
    — spans will silently no-op rather than try to reach across the
    process boundary. Caller may pass a local "observer" organism to
    record the session in the field host's own store instead.

    Every invocation emits a paired `reach_extended` /
    `reach_retracted` span pair. If the pipe call raises or the MCP
    tool returns `isError`, the retraction span carries the reason
    and the caller receives an empty string (the ResponseFn contract
    doesn't permit raising into field control flow without breaking
    the field's own error recovery).
    """
    target_name = getattr(client, "organism_name", "") or ""

    def _response_fn(participant: Any, prompt: str) -> str:
        started = time.time()
        if organism is not None:
            _trace.emit_reach_extended(
                organism,
                pipe_kind=pipe_kind,
                transport=transport,
                tool_name=TOOL_ENGINE_SUBMIT,
                field_name=field_name,
                attributes={
                    "target": getattr(participant, "name", "") or target_name,
                    "prompt_chars": len(prompt or ""),
                },
            )
        ok = True
        err = ""
        text = ""
        try:
            result = client.call_tool(TOOL_ENGINE_SUBMIT, {"prompt": prompt})
            if result.is_error:
                ok = False
                err = result.error
                text = ""
            else:
                text = result.text
        except Exception as e:
            ok = False
            err = f"call_tool raised: {e}"
            logger.debug("client.call_tool raised", exc_info=True)
        finally:
            if organism is not None:
                _trace.emit_reach_retracted(
                    organism,
                    pipe_kind=pipe_kind,
                    transport=transport,
                    tool_name=TOOL_ENGINE_SUBMIT,
                    duration_s=time.time() - started,
                    ok=ok,
                    error=err,
                    field_name=field_name,
                    attributes={
                        "target": getattr(participant, "name", "") or target_name,
                        "response_chars": len(text or ""),
                    },
                )
        return text

    return _response_fn


def make_local_mcp_response_fn(
    organism: Any,
    *,
    pipe_kind: str = "local_loopback",
    transport: str = "in_process",
    field_name: str | None = None,
) -> Callable[[Any, str], str]:
    """Phase 1 convenience: wire up a `LocalMCPClient` and hand back
    the ResponseFn. Back-compat with the Phase 1 test suite. New
    callers that want to pick the transport explicitly should use
    `make_mcp_response_fn(client, organism=organism, ...)` directly.
    """
    client = LocalMCPClient(organism)
    return make_mcp_response_fn(
        client,
        organism=organism,
        pipe_kind=pipe_kind,
        transport=transport,
        field_name=field_name,
    )
