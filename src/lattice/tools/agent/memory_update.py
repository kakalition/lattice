"""Agent tool: memory_update."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.memory.tools import memory_update
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def memory_update_tool(ctx: RunContext[TurnDeps], memory_id: str, text: str) -> str:
        if err := not_allowed(ctx, "memory_update"):
            return err
        return await traced(
            ctx,
            "memory_update",
            {"memory_id": memory_id, "text": text},
            lambda: memory_update(ctx.deps.memory, memory_id, text),
        )

    return {"memory_update": memory_update_tool}
