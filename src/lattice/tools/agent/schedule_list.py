"""Agent tool: schedule_list."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.scheduler.tools import schedule_list
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def schedule_list_tool(ctx: RunContext[TurnDeps]) -> str:
        """List scheduled reminder jobs."""
        if err := not_allowed(ctx, "schedule_list"):
            return err
        return await traced(
            ctx, "schedule_list", {}, lambda: schedule_list(home=ctx.deps.settings.home)
        )

    return {"schedule_list": schedule_list_tool}
