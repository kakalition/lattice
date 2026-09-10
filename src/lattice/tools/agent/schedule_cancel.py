"""Agent tool: schedule_cancel."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.scheduler.tools import schedule_cancel
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def schedule_cancel_tool(ctx: RunContext[TurnDeps], job_id: str) -> str:
        """Cancel a scheduled job by id."""
        if err := not_allowed(ctx, "schedule_cancel"):
            return err
        return await traced(
            ctx,
            "schedule_cancel",
            {"job_id": job_id},
            lambda: schedule_cancel(job_id, home=ctx.deps.settings.home),
        )

    return {"schedule_cancel": schedule_cancel_tool}
