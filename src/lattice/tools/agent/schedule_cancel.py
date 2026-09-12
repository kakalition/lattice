"""Agent tool: schedule_cancel."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.scheduler.tools import schedule_cancel as _schedule_cancel
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def schedule_cancel(ctx: RunContext[TurnDeps], job_id: str) -> str:
        """Cancel a scheduled job by id."""
        return await traced(
            ctx,
            "schedule_cancel",
            {"job_id": job_id},
            lambda: _schedule_cancel(job_id, home=ctx.deps.settings.home),
        )

    return {"schedule_cancel": schedule_cancel}
