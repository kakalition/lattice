"""Agent tool: tool_describe (MCP)."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.mcp import tool_describe
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def tool_describe_tool(ctx: RunContext[TurnDeps], name: str) -> str:
        if err := not_allowed(ctx, "tool_describe"):
            return err
        return await traced(
            ctx, "tool_describe", {"name": name}, lambda: tool_describe(ctx.deps.mcp, name)
        )

    return {"tool_describe": tool_describe_tool}
