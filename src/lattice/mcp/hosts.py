"""MCP host: connect to external servers, discover tools, and invoke them.

Each configured server runs a persistent worker task that owns the transport
session for the process lifetime. Discovery and calls are funnelled through the
worker's request queue, so the ``mcp`` SDK's AnyIO task scopes are always
entered and exited inside the same task.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from lattice.config import LatticeSettings, McpServerConfig
from lattice.tool_names import RESERVED_GROUPS, WIRE_NAME_RE, wire_name

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

logger = logging.getLogger("lattice.mcp")

# A server becomes the wire-name prefix, so it must be a single lowercase token
# with no ``__`` (which would break the canonical⇄wire mapping). Tool leaves may
# use the provider's wider alphabet but must never contain ``__``.
_SERVER_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_MCP_LEAF_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,48}$")


# The ``schema`` field name shadows the deprecated ``BaseModel.schema`` attribute,
# so pydantic emits a UserWarning at class-creation time. The public attribute name
# is used by ``lattice.mcp.bridge`` — keep it and silence the warning instead.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message='Field name "schema"')

    class McpToolInfo(BaseModel):
        server: str
        name: str
        description: str
        # ``schema`` intentionally shadows the deprecated BaseModel.schema attribute;
        # the public name is relied on by lattice.mcp.bridge.
        schema: dict[str, Any] = Field(default_factory=dict)  # pyright: ignore[reportIncompatibleMethodOverride]


@dataclass
class _Request:
    method: str
    payload: dict[str, Any]
    future: asyncio.Future[Any]


@dataclass
class _ServerWorker:
    """Owns one server's session; all protocol calls go through its queue."""

    server: McpServerConfig
    connect_timeout: float
    call_timeout: float
    connector: Callable[[McpServerConfig], Any]
    _queue: asyncio.Queue[_Request | None] = field(default_factory=asyncio.Queue)
    _error: Exception | None = None
    _task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self.server.name}")

    async def _run(self) -> None:
        try:
            async with self.connector(self.server) as session:
                await session.initialize()
                while True:
                    request = await self._queue.get()
                    if request is None:
                        break
                    try:
                        request.future.set_result(await self._dispatch(session, request))
                    except Exception as exc:  # noqa: BLE001 — surfaced to the caller
                        request.future.set_exception(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — connection/initialize failure
            self._error = exc
            logger.warning("mcp server %s failed: %s", self.server.name, exc)
        finally:
            self._fail_pending()

    @staticmethod
    async def _dispatch(session: Any, request: _Request) -> Any:
        if request.method == "list_tools":
            return await _list_all_tools(session)
        if request.method == "call_tool":
            return await session.call_tool(
                request.payload["name"], request.payload.get("arguments")
            )
        raise ValueError(f"unknown mcp request: {request.method}")

    def _fail_pending(self) -> None:
        while True:
            try:
                request = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if request is not None and not request.future.done():
                request.future.set_exception(
                    self._error or RuntimeError(f"mcp server {self.server.name} closed")
                )

    async def request(self, method: str, **payload: Any) -> Any:
        if self._error is not None:
            raise self._error
        if self._task is None:
            self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await self._queue.put(_Request(method, payload, future))
        # Close the race where the worker failed after our pre-enqueue check but
        # before it could drain the queue: fail this request immediately.
        if self._error is not None and not future.done():
            future.set_exception(self._error)
        timeout = self.call_timeout if method == "call_tool" else self.connect_timeout
        return await asyncio.wait_for(future, timeout=timeout)

    async def stop(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        self._queue.put_nowait(None)
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (Exception, asyncio.CancelledError):
            # Turn cancelled (or the server hung on shutdown): force it down.
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


class McpHostManager:
    """Connects to configured MCP servers and exposes their tools."""

    def __init__(
        self,
        servers: list[McpServerConfig] | None = None,
        *,
        connect_timeout: float = 20.0,
        call_timeout: float = 60.0,
        connector: Callable[[McpServerConfig], Any] | None = None,
    ) -> None:
        self.servers = list(servers or [])
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self._connector = connector or _connect_session
        self.tools: list[McpToolInfo] = []
        # Registration/connection rejections; surfaced as notices and in doctor.
        self.errors: list[str] = []
        self._workers: dict[str, _ServerWorker] = {}

    @classmethod
    def from_settings(cls, settings: LatticeSettings) -> McpHostManager:
        cfg = settings.mcp
        servers = list(cfg.servers) if cfg.enabled else []
        return cls(
            servers,
            connect_timeout=cfg.connect_timeout_seconds,
            call_timeout=cfg.call_timeout_seconds,
        )

    def configured_servers(self) -> list[McpServerConfig]:
        return [s for s in self.servers if s.enabled]

    def register_discovered(self, tools: list[McpToolInfo]) -> None:
        """Adopt tools directly (used by tests and by discovery)."""
        self.tools, self.errors = _validate_tools(tools)

    async def discover(self) -> list[McpToolInfo]:
        """Connect to every enabled server and register the tools it advertises.

        Best-effort and concurrent: a server that fails to connect contributes an
        error while the others still work, and one slow server cannot serialize
        the others. Never raises.
        """
        servers = self.configured_servers()
        errors: list[str] = []
        valid_servers: list[McpServerConfig] = []
        for server in servers:
            problem = _validate_server(server)
            if problem is not None:
                errors.append(problem)
            else:
                valid_servers.append(server)
        results = await asyncio.gather(*(self._discover_one(server) for server in valid_servers))
        discovered: list[McpToolInfo] = []
        for tools, server_errors in results:
            discovered.extend(tools)
            errors.extend(server_errors)
        valid, validation_errors = _validate_tools(discovered)
        self.tools = valid
        self.errors = [*errors, *validation_errors]
        return valid

    async def _discover_one(self, server: McpServerConfig) -> tuple[list[McpToolInfo], list[str]]:
        try:
            raw = await self._worker(server).request("list_tools")
        except Exception as exc:  # noqa: BLE001 — one server must not fail the turn
            return [], [f"mcp server {server.name!r}: {exc}"]
        return [
            McpToolInfo(
                server=server.name,
                name=str(tool.name),
                description=str(tool.description or ""),
                schema=dict(getattr(tool, "input_schema", None) or {}),
            )
            for tool in raw
        ], []

    def enabled_tools(self) -> list[McpToolInfo]:
        return list(self.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Invoke a discovered tool; ``name`` is canonical ``server/tool``."""
        server_name, _, leaf = name.partition("/")
        info = next(
            (t for t in self.tools if t.server == server_name and t.name == leaf),
            None,
        )
        if info is None:
            return f"unknown mcp tool: {name}"
        server = next((s for s in self.configured_servers() if s.name == server_name), None)
        if server is None:
            return f"error: mcp server {server_name!r} is not configured"
        try:
            result = await self._worker(server).request(
                "call_tool", name=leaf, arguments=arguments or {}
            )
        except Exception as exc:  # noqa: BLE001 — report, never raise into the agent
            return f"error: mcp {name}: {exc}"
        return _format_call_result(result)

    def _worker(self, server: McpServerConfig) -> _ServerWorker:
        worker = self._workers.get(server.name)
        if worker is None:
            worker = _ServerWorker(
                server,
                connect_timeout=self.connect_timeout,
                call_timeout=self.call_timeout,
                connector=self._connector,
            )
            self._workers[server.name] = worker
        return worker

    async def close(self) -> None:
        workers = list(self._workers.values())
        self._workers.clear()
        for worker in workers:
            await worker.stop()


def _validate_server(server: McpServerConfig) -> str | None:
    name = (server.name or "").strip()
    if not _SERVER_NAME_RE.match(name) or "__" in name:
        return f"mcp: invalid server name {name!r} (use ^[a-z][a-z0-9_-]*$ and no '__')"
    if name in RESERVED_GROUPS:
        return f"mcp: server name {name!r} is reserved by a built-in group"
    return None


def _validate_tool_info(info: McpToolInfo) -> str | None:
    """Return a rejection reason, or ``None`` when the identity is usable."""
    server = (info.server or "").strip()
    if server:
        if not _SERVER_NAME_RE.match(server):
            return f"mcp: invalid server name {server!r} (use ^[a-z][a-z0-9_-]*$)"
        if "__" in server:
            return f"mcp: server name {server!r} must not contain '__'"
        if server in RESERVED_GROUPS:
            return f"mcp: server name {server!r} is reserved by a built-in group"
    leaf = (info.name or "").strip()
    if not leaf or not _MCP_LEAF_RE.match(leaf):
        return f"mcp: invalid tool name {leaf!r}"
    if "__" in leaf:
        return f"mcp: tool name {leaf!r} must not contain '__'"
    wire = wire_name(f"{server}/{leaf}" if server else leaf)
    if not WIRE_NAME_RE.match(wire):
        return f"mcp: wire name {wire!r} violates the provider grammar"
    return None


def _validate_tools(tools: list[McpToolInfo]) -> tuple[list[McpToolInfo], list[str]]:
    accepted: list[McpToolInfo] = []
    errors: list[str] = []
    seen: set[str] = set()
    for info in tools:
        problem = _validate_tool_info(info)
        if problem is not None:
            errors.append(problem)
            continue
        canonical = f"{info.server}/{info.name}" if info.server else info.name
        if canonical in seen:
            errors.append(f"mcp: duplicate tool {canonical!r}")
            continue
        seen.add(canonical)
        accepted.append(info)
    return accepted, errors


async def _list_all_tools(session: Any) -> list[Any]:
    """Page through ``list_tools`` until the server stops returning a cursor."""
    from mcp.types import PaginatedRequestParams

    tools: list[Any] = []
    cursor: str | None = None
    while True:
        params = PaginatedRequestParams(cursor=cursor) if cursor else None
        result = await session.list_tools(params=params)
        tools.extend(result.tools or [])
        cursor = getattr(result, "next_cursor", None)
        if not cursor:
            return tools


def _format_call_result(result: Any) -> str:
    """Render an MCP call result as model-readable text."""
    from mcp.types import CallToolResult, TextContent

    if not isinstance(result, CallToolResult):
        return str(result)
    chunks: list[str] = []
    for block in result.content or []:
        if isinstance(block, TextContent):
            chunks.append(block.text)
        else:
            kind = getattr(block, "type", "content")
            mime = getattr(block, "mimeType", "")
            chunks.append(f"[{kind}{f' {mime}' if mime else ''}]")
    text = "\n".join(chunk for chunk in chunks if chunk)
    if not text and result.structured_content:
        text = json.dumps(result.structured_content, default=str)
    if result.is_error:
        return f"error: {text or 'mcp tool failed'}"
    return text or "(no content)"


@contextlib.asynccontextmanager
async def _connect_session(server: McpServerConfig) -> AsyncIterator[Any]:
    """Open a transport + initialized ``ClientSession`` for one server."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    if server.url:
        from mcp.client.streamable_http import streamable_http_client

        async with (
            streamable_http_client(server.url) as (read, write),
            ClientSession(read, write) as session,
        ):
            yield session
        return
    params = StdioServerParameters(
        command=server.command or "",
        args=list(server.args),
        env=dict(server.env) or None,
        cwd=server.cwd,
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        yield session
