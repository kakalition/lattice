"""MCP host configuration and lifecycle."""

from __future__ import annotations

import warnings
from typing import Any

from pydantic import BaseModel, Field


class McpServerConfig(BaseModel):
    name: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


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


class McpHostManager:
    """Tracks configured MCP servers and discovered tools (lite host)."""

    def __init__(self, servers: list[McpServerConfig] | None = None) -> None:
        self.servers = servers or []
        self.tools: list[McpToolInfo] = []

    def register_discovered(self, tools: list[McpToolInfo]) -> None:
        self.tools = tools

    def enabled_tools(self) -> list[McpToolInfo]:
        return list(self.tools)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Invoke a discovered tool.

        No live MCP session exists in this build, so calls report not-connected
        rather than silently succeeding.
        """
        for t in self.tools:
            if t.name == name or f"{t.server}/{t.name}" == name:
                return (
                    f"MCP invoke stub for {t.server}/{t.name} args={arguments or {}}. "
                    "Connect a live MCP session to execute."
                )
        return f"unknown mcp tool: {name}"

    async def close(self) -> None:
        return None
