"""MCP host configuration and lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpServerConfig:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class McpToolInfo:
    server: str
    name: str
    description: str
    schema: dict[str, Any] = field(default_factory=dict)


class McpHostManager:
    """Tracks configured MCP servers and discovered tools (lite host)."""

    def __init__(self, servers: list[McpServerConfig] | None = None) -> None:
        self.servers = servers or []
        self.tools: list[McpToolInfo] = []

    def register_discovered(self, tools: list[McpToolInfo]) -> None:
        self.tools = tools

    def enabled_tools(self) -> list[McpToolInfo]:
        return list(self.tools)

    async def close(self) -> None:
        return None
