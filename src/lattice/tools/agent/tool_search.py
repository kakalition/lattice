"""Agent tool: tool_search (MCP)."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.mcp import tool_search
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def tool_search_tool(ctx: RunContext[TurnDeps], query: str) -> str:
        if err := not_allowed(ctx, "tool_search"):
            return err
        return await traced(
            ctx, "tool_search", {"query": query}, lambda: tool_search(ctx.deps.mcp, query)
        )

    return {"tool_search": tool_search_tool}
