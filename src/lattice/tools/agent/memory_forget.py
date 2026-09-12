"""Agent tool: memory_forget."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.memory.tools import memory_forget as _memory_forget
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def memory_forget(ctx: RunContext[TurnDeps], memory_id: str) -> str:
        return await traced(
            ctx,
            "memory_forget",
            {"memory_id": memory_id},
            lambda: _memory_forget(ctx.deps.memory, memory_id),
        )

    return {"memory_forget": memory_forget}
