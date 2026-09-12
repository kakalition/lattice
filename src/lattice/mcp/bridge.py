"""Deferred MCP bridge: decides whether discovered MCP tools are hidden behind tool search."""

from __future__ import annotations

from lattice.config import McpDeferMode, ToolsConfig
from lattice.mcp.hosts import McpHostManager


def should_defer_mcp(manager: McpHostManager, tools_cfg: ToolsConfig) -> bool:
    if tools_cfg.mcp_defer == McpDeferMode.ALWAYS:
        return True
    if tools_cfg.mcp_defer == McpDeferMode.NEVER:
        return False
    return len(manager.enabled_tools()) > tools_cfg.mcp_defer_threshold
