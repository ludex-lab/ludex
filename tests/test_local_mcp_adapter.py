"""Tests for `ludex.mcp.local_adapter` — D-062 Phases 1 + 2a.

- Phase 1: `LocalMCPClient` (in-process) + `make_local_mcp_response_fn`.
  Verifies that a field holding only a ResponseFn (not an organism
  reference) can drive a creature through the MCP-shaped call surface,
  and that the reach lifecycle is traced.
- Phase 2a: `SubprocessMCPClient` (real stdio) + shared
  `make_mcp_response_fn` factory. Verifies the MCP result marshal,
  the lifecycle error paths, and an end-to-end subprocess round-trip
  against a minimal canned MCP server.

The Phase 1 tests use stubbed engines — no real LLM calls — so the
focus is on the adapter's contract (ResponseFn shape, error
marshaling, span emission). The Phase 2a integration test spawns a
tiny self-contained MCP server (written to a tmp file) so we don't
need to build a full Ludex organism inside a subprocess.
"""
from __future__ import annotations

import os
import sys
import textwrap
from dataclasses import dataclass
from typing import Any

import pytest

from ludex.fields.conversation import Participant
from ludex.mcp.local_adapter import (
    LocalMCPClient,
    SubprocessMCPClient,
    ToolResult,
    TOOL_ENGINE_SUBMIT,
    _tool_result_from_mcp,
    make_local_mcp_response_fn,
    make_mcp_response_fn,
)


# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------

@dataclass
class _TurnResult:
    response: str = ""


class _StubEngine:
    """Canned-response engine. Records calls for assertions."""

    def __init__(self, response: str = "", raise_on_call: Exception | None = None):
        self._response = response
        self._raise = raise_on_call
        self.calls: list[dict[str, Any]] = []

    def handle_submit(self, prompt: str, system: str = "",
                      tools=None, tool_dispatcher=None):
        self.calls.append({"prompt": prompt, "system": system})
        if self._raise is not None:
            raise self._raise
        return _TurnResult(response=self._response)


class _StubStore:
    """Collects spans in-memory so tests can assert on the trace output
    without touching disk. Matches the small slice of LudexStore that
    trace.emit_* uses (just `.append(span)`)."""

    def __init__(self):
        self.spans: list[Any] = []

    def append(self, span):
        self.spans.append(span)


class _StubConfig(dict):
    """dict with .get — matches organism.config shape."""


class _StubOrganism:
    def __init__(self, name: str, engine=None, store=None, habitat_dir: str = ""):
        self.name = name
        self._engine = engine
        self._store = store
        self.config = _StubConfig()
        if habitat_dir:
            self.config["habitat_dir"] = habitat_dir

    def get_block(self, name: str):
        if name == "engine":
            return self._engine
        return None


@pytest.fixture
def patch_store(monkeypatch):
    """Redirect `trace._store_for` to return a per-organism StubStore so
    `emit_*` helpers succeed even without a persistent habitat on disk.
    """
    from ludex.core import trace as _trace
    stores_by_name: dict[str, _StubStore] = {}

    def _fake_store_for(organism):
        name = getattr(organism, "name", "anon")
        store = stores_by_name.setdefault(name, _StubStore())
        # Attach on the organism for easy introspection.
        organism._store = store
        return store

    monkeypatch.setattr(_trace, "_store_for", _fake_store_for)
    return stores_by_name


# ======================================================================
# LocalMCPClient — direct tool dispatch
# ======================================================================

def test_client_dispatches_engine_submit_to_engine():
    engine = _StubEngine(response="hello from engine")
    org = _StubOrganism("Hearth", engine=engine)
    client = LocalMCPClient(org)

    result = client.call_tool(TOOL_ENGINE_SUBMIT, {"prompt": "hi"})

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert result.text == "hello from engine"
    assert engine.calls == [{"prompt": "hi", "system": ""}]


def test_client_passes_system_prompt_through():
    engine = _StubEngine(response="ok")
    org = _StubOrganism("Hearth", engine=engine)
    client = LocalMCPClient(org)

    client.call_tool(TOOL_ENGINE_SUBMIT, {"prompt": "p", "system": "S"})

    assert engine.calls[0] == {"prompt": "p", "system": "S"}


def test_client_returns_error_when_engine_missing():
    org = _StubOrganism("Hearth", engine=None)
    client = LocalMCPClient(org)

    result = client.call_tool(TOOL_ENGINE_SUBMIT, {"prompt": "hi"})

    assert result.is_error is True
    assert "Engine" in result.error
    assert result.text == ""


def test_client_reports_engine_exception_as_tool_error():
    engine = _StubEngine(raise_on_call=RuntimeError("boom"))
    org = _StubOrganism("Hearth", engine=engine)
    client = LocalMCPClient(org)

    result = client.call_tool(TOOL_ENGINE_SUBMIT, {"prompt": "hi"})

    assert result.is_error is True
    assert "boom" in result.error


def test_client_rejects_unknown_tool():
    org = _StubOrganism("Hearth", engine=_StubEngine("_"))
    client = LocalMCPClient(org)

    result = client.call_tool("ludex_nonexistent_tool", {})

    assert result.is_error is True
    assert "Unknown tool" in result.error


def test_client_requires_organism():
    with pytest.raises(ValueError):
        LocalMCPClient(None)


# ======================================================================
# make_local_mcp_response_fn — field-facing surface
# ======================================================================

def test_response_fn_returns_engine_text(patch_store):
    engine = _StubEngine(response="the creature speaks")
    org = _StubOrganism("Hearth", engine=engine)
    fn = make_local_mcp_response_fn(org)

    reply = fn(Participant(name="Hearth", organism=org), "prompt")

    assert reply == "the creature speaks"
    assert engine.calls == [{"prompt": "prompt", "system": ""}]


def test_response_fn_has_field_responsefn_signature(patch_store):
    """Smoke: the returned callable is swap-compatible with the
    existing ResponseFn alias (Callable[[Participant, str], str]).
    We check the shape by calling it and asserting a string comes back
    — not a richer object that would break Forum/Academy consumers."""
    org = _StubOrganism("X", engine=_StubEngine(response="hi"))
    fn = make_local_mcp_response_fn(org)
    out = fn(Participant(name="X", organism=org), "ping")
    assert isinstance(out, str)


def test_response_fn_emits_reach_extended_and_retracted(patch_store):
    engine = _StubEngine(response="ok")
    org = _StubOrganism("Hearth", engine=engine)
    fn = make_local_mcp_response_fn(org, field_name="TestField")

    fn(Participant(name="Hearth", organism=org), "hello")

    store = patch_store["Hearth"]
    kinds = [s.kind for s in store.spans]
    assert kinds == ["reach_extended", "reach_retracted"]

    ext, ret = store.spans
    assert ext.attributes["pipe_kind"] == "local_loopback"
    assert ext.attributes["transport"] == "in_process"
    assert ext.attributes["tool"] == "ludex_engine_submit"
    assert ext.attributes["field_name"] == "TestField"
    assert ext.attributes["prompt_chars"] == len("hello")

    assert ret.attributes["ok"] is True
    assert ret.attributes["error"] == ""
    assert ret.attributes["response_chars"] == len("ok")
    assert ret.attributes["duration_s"] is not None
    assert ret.attributes["duration_s"] >= 0.0


def test_response_fn_swallows_engine_errors_and_tags_retraction(patch_store):
    """Fields must not see the adapter raise — ResponseFn returns an
    empty string, and the reach_retracted span carries the error so
    ethnography can reconstruct what happened."""
    engine = _StubEngine(raise_on_call=RuntimeError("brain offline"))
    org = _StubOrganism("Flint", engine=engine)
    fn = make_local_mcp_response_fn(org)

    reply = fn(Participant(name="Flint", organism=org), "hi")

    assert reply == ""
    store = patch_store["Flint"]
    kinds = [s.kind for s in store.spans]
    assert kinds == ["reach_extended", "reach_retracted"]
    ret = store.spans[-1]
    assert ret.attributes["ok"] is False
    assert "brain offline" in ret.attributes["error"]


def test_response_fn_is_independent_per_organism(patch_store):
    """Multiple creatures on the same machine: each adapter dispatches
    to its own organism. Verifies we don't leak state through a shared
    global (contrast with `ludex_mcp_server._organism`)."""
    h_engine = _StubEngine(response="from Hearth")
    f_engine = _StubEngine(response="from Flint")
    h = _StubOrganism("Hearth", engine=h_engine)
    f = _StubOrganism("Flint", engine=f_engine)

    fn_h = make_local_mcp_response_fn(h)
    fn_f = make_local_mcp_response_fn(f)

    reply_h = fn_h(Participant(name="Hearth", organism=h), "a")
    reply_f = fn_f(Participant(name="Flint", organism=f), "b")

    assert reply_h == "from Hearth"
    assert reply_f == "from Flint"
    assert h_engine.calls == [{"prompt": "a", "system": ""}]
    assert f_engine.calls == [{"prompt": "b", "system": ""}]


# ======================================================================
# End-to-end: drive a minimal 2-creature exchange via loopback
# ======================================================================

def test_loopback_dyad_exchange(patch_store):
    """Minimal two-creature exchange fully driven by ResponseFn.
    Mirrors the Phase 1 smoke test goal from the design doc: "Hearth vs
    Flint in a Wilderness, both invoked via loopback MCP." We don't
    spin up a full Wilderness here — just verify the round-trip.
    """
    h_engine = _StubEngine(response="I propose X")
    f_engine = _StubEngine(response="I disagree with X")
    h = _StubOrganism("Hearth", engine=h_engine)
    f = _StubOrganism("Flint", engine=f_engine)

    fn_h = make_local_mcp_response_fn(h, field_name="Wilderness-Smoke")
    fn_f = make_local_mcp_response_fn(f, field_name="Wilderness-Smoke")

    p_h = Participant(name="Hearth", organism=h)
    p_f = Participant(name="Flint", organism=f)

    # Turn 1: Hearth opens.
    open_msg = fn_h(p_h, "What do you propose?")
    # Turn 2: Flint responds to Hearth's open.
    reply_msg = fn_f(p_f, open_msg)

    assert "propose" in open_msg
    assert "disagree" in reply_msg

    # Both creatures emitted paired reach spans — no reference leaks
    # between stores.
    for name in ("Hearth", "Flint"):
        store = patch_store[name]
        kinds = [s.kind for s in store.spans]
        assert kinds == ["reach_extended", "reach_retracted"]


# ======================================================================
# _tool_result_from_mcp — result marshaling (Phase 2a)
# ======================================================================

@dataclass
class _FakeTextContent:
    text: str


@dataclass
class _FakeMCPResult:
    content: list
    isError: bool = False


def test_marshal_joins_text_content_items():
    mcp = _FakeMCPResult(
        content=[_FakeTextContent("hello "), _FakeTextContent("world")],
        isError=False,
    )
    out = _tool_result_from_mcp(mcp)
    assert out.is_error is False
    assert out.text == "hello world"
    assert out.error == ""


def test_marshal_drops_non_text_content_items():
    @dataclass
    class _FakeImage:
        data: bytes = b"\x00"

    mcp = _FakeMCPResult(
        content=[_FakeTextContent("visible"), _FakeImage()],
        isError=False,
    )
    out = _tool_result_from_mcp(mcp)
    assert out.text == "visible"


def test_marshal_treats_isError_as_error_with_text_message():
    mcp = _FakeMCPResult(
        content=[_FakeTextContent("engine exploded")],
        isError=True,
    )
    out = _tool_result_from_mcp(mcp)
    assert out.is_error is True
    assert out.text == ""
    assert "engine exploded" in out.error


def test_marshal_handles_empty_content_list():
    mcp = _FakeMCPResult(content=[], isError=False)
    out = _tool_result_from_mcp(mcp)
    assert out.text == ""
    assert out.is_error is False


# ======================================================================
# SubprocessMCPClient — lifecycle & error paths (unit)
# ======================================================================

def test_subprocess_client_requires_habitat_dir():
    with pytest.raises(ValueError):
        SubprocessMCPClient(habitat_dir="")


def test_subprocess_client_call_tool_before_start_returns_error():
    client = SubprocessMCPClient(habitat_dir=".", organism_name="X")
    result = client.call_tool(TOOL_ENGINE_SUBMIT, {"prompt": "hi"})
    assert result.is_error is True
    assert "not started" in result.error


def test_subprocess_client_organism_name_defaults_to_habitat_basename(tmp_path):
    habitat = tmp_path / "MyCreature"
    habitat.mkdir()
    client = SubprocessMCPClient(habitat_dir=str(habitat))
    assert client.organism_name == "MyCreature"


def test_subprocess_client_server_argv_includes_engine_flag(tmp_path):
    habitat = tmp_path / "C"
    habitat.mkdir()
    client = SubprocessMCPClient(
        habitat_dir=str(habitat),
        python_executable="python3",
    )
    argv = client._server_argv()
    assert argv[0] == "python3"
    assert "-m" in argv
    assert "ludex.mcp.ludex_mcp_server" in argv
    assert "--habitat" in argv
    assert str(habitat.resolve()).lower() == os.path.abspath(str(habitat)).lower()
    assert "--enable-engine" in argv


# ======================================================================
# SubprocessMCPClient — real subprocess integration
# ----------------------------------------------------------------------
# These tests spawn a tiny canned MCP server from a generated script.
# They prove the stdio wire + ClientSession.call_tool round-trip
# without booting a full Ludex organism in the subprocess. A separate
# manual smoke against the real ludex_mcp_server lives in the Phase 2a
# commit doc — it needs a habitat with a working brain and is better
# run by hand than in CI.
# ======================================================================

_CANNED_SERVER_SCRIPT = textwrap.dedent("""
    import asyncio
    import sys
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent

    server = Server("canned-ludex")

    @server.list_tools()
    async def _list():
        return [
            Tool(
                name="ludex_engine_submit",
                description="canned",
                inputSchema={
                    "type": "object",
                    "properties": {"prompt": {"type": "string"}},
                    "required": ["prompt"],
                },
            ),
            Tool(
                name="ludex_fail_tool",
                description="always errors",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def _call(name, arguments):
        if name == "ludex_engine_submit":
            p = (arguments or {}).get("prompt", "")
            return [TextContent(type="text", text=f"echo:{p}")]
        if name == "ludex_fail_tool":
            # Returning the isError envelope requires lower-level control;
            # raise so the server turns it into a protocol-level error
            # content with isError=True.
            raise RuntimeError("boom")
        return [TextContent(type="text", text="unknown tool")]

    async def _main():
        async with stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())

    asyncio.run(_main())
""")


@pytest.fixture
def canned_server_script(tmp_path):
    path = tmp_path / "canned_server.py"
    path.write_text(_CANNED_SERVER_SCRIPT, encoding="utf-8")
    return str(path)


def _spawn_canned(script_path: str, *, habitat_dir: str) -> SubprocessMCPClient:
    """Subclass-free trick: SubprocessMCPClient's argv builder assumes
    the ludex_mcp_server CLI, so we subclass in-line for the canned
    server. The stdio + event-loop plumbing is what's under test."""

    class _CannedClient(SubprocessMCPClient):
        def _server_argv(self):
            return [self._python, script_path]

    client = _CannedClient(
        habitat_dir=habitat_dir,
        organism_name="canned",
        startup_timeout_s=30.0,
        call_timeout_s=30.0,
    )
    client.start()
    return client


def test_subprocess_round_trip_engine_submit(tmp_path, canned_server_script):
    client = _spawn_canned(canned_server_script, habitat_dir=str(tmp_path))
    try:
        result = client.call_tool(TOOL_ENGINE_SUBMIT, {"prompt": "hello"})
        assert result.is_error is False
        assert result.text == "echo:hello"
    finally:
        client.close()


def test_subprocess_tool_raising_marshals_as_error(tmp_path, canned_server_script):
    client = _spawn_canned(canned_server_script, habitat_dir=str(tmp_path))
    try:
        result = client.call_tool("ludex_fail_tool", {})
        assert result.is_error is True
        assert "boom" in result.error
    finally:
        client.close()


def test_subprocess_close_is_idempotent(tmp_path, canned_server_script):
    client = _spawn_canned(canned_server_script, habitat_dir=str(tmp_path))
    client.close()
    client.close()  # must not raise


def test_subprocess_context_manager(tmp_path, canned_server_script):
    class _CannedClient(SubprocessMCPClient):
        def _server_argv(self):
            return [self._python, canned_server_script]

    with _CannedClient(
        habitat_dir=str(tmp_path),
        organism_name="canned",
        startup_timeout_s=30.0,
        call_timeout_s=30.0,
    ) as client:
        result = client.call_tool(TOOL_ENGINE_SUBMIT, {"prompt": "ctx"})
        assert result.text == "echo:ctx"


# ======================================================================
# make_mcp_response_fn — shared factory across transports
# ======================================================================

def test_make_mcp_response_fn_with_subprocess_client(
    tmp_path, canned_server_script, patch_store
):
    """The factory works the same with a subprocess client — the
    Phase 2a proof point. Spans emit against a passed-in observer
    organism so ethnography still captures the reach even when the
    target creature lives in another process."""
    observer = _StubOrganism("FieldHost")
    client = _spawn_canned(canned_server_script, habitat_dir=str(tmp_path))
    try:
        fn = make_mcp_response_fn(
            client,
            organism=observer,
            pipe_kind="stdio_mcp",
            transport="stdio_subprocess",
            field_name="Phase2a-Smoke",
        )
        reply = fn(Participant(name="target", organism=None), "ping")
        assert reply == "echo:ping"
    finally:
        client.close()

    store = patch_store["FieldHost"]
    kinds = [s.kind for s in store.spans]
    assert kinds == ["reach_extended", "reach_retracted"]
    ext, ret = store.spans
    assert ext.attributes["pipe_kind"] == "stdio_mcp"
    assert ext.attributes["transport"] == "stdio_subprocess"
    assert ret.attributes["ok"] is True
    assert ret.attributes["response_chars"] == len("echo:ping")


def test_make_mcp_response_fn_without_organism_skips_spans(
    tmp_path, canned_server_script
):
    """When organism=None (the target creature lives only in the
    subprocess and we have no local observer), spans are skipped —
    the call still round-trips normally."""
    client = _spawn_canned(canned_server_script, habitat_dir=str(tmp_path))
    try:
        fn = make_mcp_response_fn(client, organism=None)
        reply = fn(Participant(name="target", organism=None), "ping")
        assert reply == "echo:ping"
    finally:
        client.close()
