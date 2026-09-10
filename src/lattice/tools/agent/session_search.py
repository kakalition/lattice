"""Agent tool: session_search."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.session_search import session_search


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def session_search_tool(ctx: RunContext[TurnDeps], query: str) -> str:
        if err := not_allowed(ctx, "session_search"):
            return err
        return await traced(
            ctx,
            "session_search",
            {"query": query},
            lambda: session_search(query, ctx.deps.session),
        )

    return {"session_search": session_search_tool}
