"""Agent tool: schedule_list."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.scheduler.tools import schedule_list as _schedule_list
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def schedule_list(ctx: RunContext[TurnDeps]) -> str:
        """List scheduled reminder jobs."""
        return await traced(
            ctx, "schedule_list", {}, lambda: _schedule_list(home=ctx.deps.settings.home)
        )

    return {"schedule_list": schedule_list}
