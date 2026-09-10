"""Agent tool: memory_forget."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.memory.tools import memory_forget
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def memory_forget_tool(ctx: RunContext[TurnDeps], memory_id: str) -> str:
        if err := not_allowed(ctx, "memory_forget"):
            return err
        return await traced(
            ctx,
            "memory_forget",
            {"memory_id": memory_id},
            lambda: memory_forget(ctx.deps.memory, memory_id),
        )

    return {"memory_forget": memory_forget_tool}
