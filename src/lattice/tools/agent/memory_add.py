"""Agent tool: memory_add."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.memory.tools import memory_add
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def memory_add_tool(ctx: RunContext[TurnDeps], text: str) -> str:
        if err := not_allowed(ctx, "memory_add"):
            return err
        return await traced(
            ctx, "memory_add", {"text": text}, lambda: memory_add(ctx.deps.memory, text)
        )

    return {"memory_add": memory_add_tool}
