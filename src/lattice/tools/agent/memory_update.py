"""Agent tool: memory_update."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.memory.tools import memory_update as _memory_update
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def memory_update(ctx: RunContext[TurnDeps], memory_id: str, text: str) -> str:
        return await traced(
            ctx,
            "memory_update",
            {"memory_id": memory_id, "text": text},
            lambda: _memory_update(ctx.deps.memory, memory_id, text),
        )

    return {"memory_update": memory_update}
