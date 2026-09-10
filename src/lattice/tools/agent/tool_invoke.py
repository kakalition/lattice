"""Agent tool: tool_invoke (MCP)."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.mcp import tool_invoke
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def tool_invoke_tool(
        ctx: RunContext[TurnDeps], name: str, arguments: dict[str, Any] | None = None
    ) -> str:
        if err := not_allowed(ctx, "tool_invoke"):
            return err
        return await traced(
            ctx,
            "tool_invoke",
            {"name": name, "arguments": arguments or {}},
            lambda: tool_invoke(ctx.deps.mcp, name, arguments),
        )

    return {"tool_invoke": tool_invoke_tool}
