"""Real MCP host: discovery, invocation, validation, and result rendering.

The transport is replaced with an in-process fake so these run offline.
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

from lattice.config import LatticeSettings, McpServerConfig
from lattice.events import NullTurnEvents
from lattice.mcp.hosts import McpHostManager
from lattice.mcp.toolset import McpToolset


class _FakeTool:
    def __init__(self, name: str, description: str = "", input_schema: dict | None = None):
        self.name = name
        self.description = description
        self.input_schema = input_schema or {"type": "object", "properties": {}}


class _FakeListResult:
    def __init__(self, tools: list[_FakeTool], next_cursor: str | None = None):
        self.tools = tools
        self.next_cursor = next_cursor


def _call_result(text: str = "ok", *, is_error: bool = False):
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(content=[TextContent(type="text", text=text)], isError=is_error)


class _FakeSession:
    def __init__(
        self,
        tools: list[_FakeTool],
        *,
        call: Any | None = None,
        pages: list[list[_FakeTool]] | None = None,
    ) -> None:
        self.tools = tools
        self.call = call if call is not None else _call_result()
        self.pages = pages
        self.initialized = False
        self.calls: list[tuple[str, dict | None]] = []

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self, params: Any = None) -> _FakeListResult:
        if self.pages is not None:
            index = int(getattr(params, "cursor", "0") or "0")
            tools = self.pages[index] if index < len(self.pages) else []
            cursor = str(index + 1) if index + 1 < len(self.pages) else None
            return _FakeListResult(tools, cursor)
        return _FakeListResult(self.tools)

    async def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        self.calls.append((name, arguments))
        return self.call


def _connector(session: _FakeSession):
    @contextlib.asynccontextmanager
    async def _connect(_server: McpServerConfig):
        yield session

    return _connect


def _server(name: str = "demo", **kwargs: Any) -> McpServerConfig:
    kwargs.setdefault("command", "fake")
    return McpServerConfig(name=name, **kwargs)


@pytest.mark.asyncio
async def test_discover_registers_tools_from_a_server() -> None:
    session = _FakeSession([_FakeTool("read", "Read a file")])
    mgr = McpHostManager([_server()], connector=_connector(session))
    tools = await mgr.discover()
    assert [t.name for t in tools] == ["read"]
    assert tools[0].server == "demo"
    assert tools[0].description == "Read a file"
    assert session.initialized
    assert mgr.errors == []
    await mgr.close()


@pytest.mark.asyncio
async def test_discover_pages_through_cursor() -> None:
    session = _FakeSession([], pages=[[_FakeTool("a")], [_FakeTool("b"), _FakeTool("c")]])
    mgr = McpHostManager([_server()], connector=_connector(session))
    tools = await mgr.discover()
    assert [t.name for t in tools] == ["a", "b", "c"]
    await mgr.close()


@pytest.mark.asyncio
async def test_call_tool_routes_and_renders_text() -> None:
    session = _FakeSession([_FakeTool("read")], call=_call_result("hello"))
    mgr = McpHostManager([_server()], connector=_connector(session))
    await mgr.discover()
    out = await mgr.call_tool("demo/read", {"path": "a.txt"})
    assert out == "hello"
    assert session.calls == [("read", {"path": "a.txt"})]
    await mgr.close()


@pytest.mark.asyncio
async def test_call_tool_marks_errors() -> None:
    session = _FakeSession([_FakeTool("read")], call=_call_result("boom", is_error=True))
    mgr = McpHostManager([_server()], connector=_connector(session))
    await mgr.discover()
    assert await mgr.call_tool("demo/read", {}) == "error: boom"
    await mgr.close()


@pytest.mark.asyncio
async def test_reserved_server_name_is_rejected() -> None:
    session = _FakeSession([_FakeTool("read")])
    mgr = McpHostManager([_server("sqlite")], connector=_connector(session))
    tools = await mgr.discover()
    assert tools == []
    assert mgr.errors and "reserved" in mgr.errors[0]
    await mgr.close()


@pytest.mark.asyncio
async def test_invalid_tool_names_are_rejected() -> None:
    session = _FakeSession([_FakeTool("good"), _FakeTool("bad__name")])
    mgr = McpHostManager([_server()], connector=_connector(session))
    tools = await mgr.discover()
    assert [t.name for t in tools] == ["good"]
    assert any("must not contain '__'" in e for e in mgr.errors)
    await mgr.close()


@pytest.mark.asyncio
async def test_unreachable_server_is_reported_not_raised() -> None:
    @contextlib.asynccontextmanager
    async def _broken(_server: McpServerConfig):
        raise RuntimeError("cannot spawn")
        yield  # pragma: no cover

    mgr = McpHostManager([_server()], connector=_broken)
    tools = await mgr.discover()
    assert tools == []
    assert any("cannot spawn" in e for e in mgr.errors)
    await mgr.close()


@pytest.mark.asyncio
async def test_toolset_exposes_wire_names_and_routes_calls() -> None:
    from dataclasses import dataclass

    @dataclass
    class _Ctx:
        max_retries: int = 1

    session = _FakeSession([_FakeTool("read")], call=_call_result("body"))
    mgr = McpHostManager([_server()], connector=_connector(session))
    await mgr.discover()
    toolset = McpToolset(mgr)
    tools = await toolset.get_tools(_Ctx())
    assert set(tools) == {"demo__read"}
    out = await toolset.call_tool("demo__read", {"path": "a"}, _Ctx(), tools["demo__read"])
    assert out == "body"
    assert session.calls == [("read", {"path": "a"})]
    await mgr.close()


@pytest.mark.asyncio
async def test_real_stdio_missing_command_reports_error() -> None:
    """A real (missing) stdio binary is reported, not raised."""
    mgr = McpHostManager(
        [_server("bad", command="lattice-no-such-binary-xyz")], connect_timeout=5.0
    )
    tools = await mgr.discover()
    assert tools == []
    assert mgr.errors
    await mgr.close()


@pytest.mark.asyncio
async def test_real_stdio_server_discovery_and_call(tmp_path: Any) -> None:
    """End-to-end against a real stdio MCP server subprocess."""
    import sys

    pytest.importorskip("mcp.server.mcpserver")
    script = tmp_path / "server.py"
    script.write_text(
        "from mcp.server.mcpserver import MCPServer\n"
        'mcp = MCPServer("demo")\n'
        "\n"
        "@mcp.tool()\n"
        "def add(a: int, b: int) -> int:\n"
        '    """Add two integers."""\n'
        "    return a + b\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    mcp.run()\n",
        encoding="utf-8",
    )
    mgr = McpHostManager(
        [_server("demo", command=sys.executable, args=[str(script)])],
        connect_timeout=30.0,
        call_timeout=30.0,
    )
    tools = await mgr.discover()
    assert [t.name for t in tools] == ["add"]
    assert tools[0].schema.get("properties", {}).get("a")
    assert await mgr.call_tool("demo/add", {"a": 2, "b": 40}) == "42"
    await mgr.close()


def test_from_settings_builds_servers() -> None:
    settings = LatticeSettings(
        mcp={
            "enabled": True,
            "servers": [{"name": "demo", "command": "npx", "args": ["-y", "pkg"]}],
        }
    )
    mgr = McpHostManager.from_settings(settings)
    assert [s.name for s in mgr.configured_servers()] == ["demo"]


def test_server_requires_exactly_one_transport() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        McpServerConfig(name="x", command="a", url="http://b")
    with pytest.raises(ValidationError):
        McpServerConfig(name="x")


class _RecordingEvents(NullTurnEvents):
    def __init__(self) -> None:
        self.starts: list[str] = []
        self.ends: list[str] = []

    async def on_tool_start(self, name: str, args: dict) -> None:
        self.starts.append(name)

    async def on_tool_end(self, name: str, result: str) -> None:
        self.ends.append(name)


class _RecordingHitl:
    def __init__(self, decision: Any) -> None:
        from lattice.hitl import ApprovalDecision

        self.decision = decision if decision is not None else ApprovalDecision.DENY
        self.calls: list[str] = []

    async def approve(self, req: Any) -> Any:
        self.calls.append(req.tool_name)
        return self.decision

    async def clarify(self, req: Any) -> str:  # pragma: no cover - unused
        return "ok"


def _deps(tmp_path: Any, hitl: Any, events: Any) -> Any:
    from lattice.config import LatticeSettings
    from lattice.deps import TurnDeps
    from lattice.memory import InMemoryMemory
    from lattice.profiles.load import Profile
    from lattice.session import SessionStore
    from lattice.sqlite import SqlitePool, SqliteRegistry

    settings = LatticeSettings(home=tmp_path)
    registry = SqliteRegistry(settings)
    return TurnDeps(
        settings=settings,
        profile=Profile(id="default"),
        hitl=hitl,
        session=SessionStore(tmp_path / "state.db"),
        session_id="s",
        memory=InMemoryMemory("t"),
        sqlite_registry=registry,
        sqlite_pool=SqlitePool(registry),
        mcp=McpHostManager(),
        events=events,
        workspace=tmp_path,
    )


@pytest.mark.asyncio
async def test_guarded_mcp_tool_uses_same_hitl_and_trace_seam(tmp_path: Any) -> None:
    """External MCP tools run through the identical precheck/approval/trace path."""
    from types import SimpleNamespace

    from lattice.agent_app import resolve_tool_policy
    from lattice.hitl import ApprovalDecision
    from lattice.tools.middleware import GuardedToolset, ToolPolicy

    session = _FakeSession([_FakeTool("add")], call=_call_result("42"))
    mgr = McpHostManager([_server("demo")], connector=_connector(session))
    await mgr.discover()

    # A policy that demands approval proves HITL reaches MCP calls.
    events = _RecordingEvents()
    hitl = _RecordingHitl(ApprovalDecision.DENY)
    ctx = SimpleNamespace(deps=_deps(tmp_path, hitl, events), max_retries=1)
    guarded = GuardedToolset(
        wrapped=McpToolset(mgr),
        resolve=lambda _ctx, _name: ToolPolicy(needs=lambda *_a: True),
    )
    assert await guarded.call_tool("demo__add", {"a": 1}, ctx, None) == "denied: deny"
    assert hitl.calls == ["demo/add"]

    # With no policy override, the default path executes and is traced uniformly.
    events2 = _RecordingEvents()
    hitl2 = _RecordingHitl(ApprovalDecision.APPROVE)
    ctx2 = SimpleNamespace(deps=_deps(tmp_path, hitl2, events2), max_retries=1)
    guarded2 = GuardedToolset(wrapped=McpToolset(mgr), resolve=resolve_tool_policy)
    assert await guarded2.call_tool("demo__add", {"a": 1}, ctx2, None) == "42"
    assert events2.starts == ["demo/add"]
    assert events2.ends == ["demo/add"]
    await mgr.close()
