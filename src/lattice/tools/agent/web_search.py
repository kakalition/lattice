"""Agent tool: web_search."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolTier, ToolsetT
from lattice.tools.web import web_search as _web_search

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def web_search(ctx: RunContext[TurnDeps], query: str) -> str:
        return await traced(
            ctx,
            "web_search",
            {"query": query},
            lambda: _web_search(query, api_key=ctx.deps.settings.tavily_api_key),
        )

    return {"web_search": web_search}
