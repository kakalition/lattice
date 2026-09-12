"""Agent tool: session_search."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolTier, ToolsetT
from lattice.tools.session_search import session_search as _session_search

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def session_search(ctx: RunContext[TurnDeps], query: str) -> str:
        return await traced(
            ctx,
            "session_search",
            {"query": query},
            lambda: _session_search(query, ctx.deps.session),
        )

    return {"session_search": session_search}
