"""Agent tool: memory_add."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.memory.tools import memory_add as _memory_add
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def memory_add(ctx: RunContext[TurnDeps], text: str) -> str:
        return await traced(
            ctx, "memory_add", {"text": text}, lambda: _memory_add(ctx.deps.memory, text)
        )

    return {"memory_add": memory_add}
