"""Agent tool: web_search."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.web import web_search


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def web_search_tool(ctx: RunContext[TurnDeps], query: str) -> str:
        if err := not_allowed(ctx, "web_search"):
            return err
        return await traced(
            ctx,
            "web_search",
            {"query": query},
            lambda: web_search(query, api_key=ctx.deps.settings.tavily_api_key),
        )

    return {"web_search": web_search_tool}
