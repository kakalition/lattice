"""Agent tool: memory_search."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.memory.tools import memory_search
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def memory_search_tool(ctx: RunContext[TurnDeps], query: str) -> str:
        if err := not_allowed(ctx, "memory_search"):
            return err
        return await traced(
            ctx, "memory_search", {"query": query}, lambda: memory_search(ctx.deps.memory, query)
        )

    return {"memory_search": memory_search_tool}
