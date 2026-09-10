"""Deferred MCP bridge: tool_search / tool_describe / invoke."""

from __future__ import annotations

from lattice.config import McpDeferMode, ToolsConfig
from lattice.mcp.hosts import McpHostManager


def should_defer_mcp(manager: McpHostManager, tools_cfg: ToolsConfig) -> bool:
    if tools_cfg.mcp_defer == McpDeferMode.ALWAYS:
        return True
    if tools_cfg.mcp_defer == McpDeferMode.NEVER:
        return False
    return len(manager.enabled_tools()) > tools_cfg.mcp_defer_threshold


def tool_search(manager: McpHostManager, query: str) -> str:
    q = query.lower()
    hits = [
        t
        for t in manager.enabled_tools()
        if q in t.name.lower() or q in t.description.lower() or q in t.server.lower()
    ]
    if not hits:
        return "(no mcp tools matched)"
    return "\n".join(f"{t.server}/{t.name}: {t.description}" for t in hits[:20])


def tool_describe(manager: McpHostManager, name: str) -> str:
    for t in manager.enabled_tools():
        if t.name == name or f"{t.server}/{t.name}" == name:
            return f"{t.server}/{t.name}\n{t.description}\nschema={t.schema}"
    return f"unknown mcp tool: {name}"


async def tool_invoke(manager: McpHostManager, name: str, arguments: dict | None = None) -> str:
    # Lite bridge: without live MCP sessions, report not connected
    for t in manager.enabled_tools():
        if t.name == name or f"{t.server}/{t.name}" == name:
            return (
                f"MCP invoke stub for {t.server}/{t.name} args={arguments or {}}. "
                "Connect a live MCP session to execute."
            )
    return f"unknown mcp tool: {name}"


def bridge_tool_names() -> list[str]:
    return ["tool_search", "tool_describe", "tool_invoke"]
