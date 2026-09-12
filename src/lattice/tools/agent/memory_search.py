"""Agent tool: memory_search."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.memory.tools import memory_search as _memory_search
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def memory_search(ctx: RunContext[TurnDeps], query: str) -> str:
        return await traced(
            ctx, "memory_search", {"query": query}, lambda: _memory_search(ctx.deps.memory, query)
        )

    return {"memory_search": memory_search}
