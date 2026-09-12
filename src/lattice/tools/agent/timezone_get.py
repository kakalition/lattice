"""Agent tool: timezone_get."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.scheduler.tools import timezone_get as _timezone_get
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def timezone_get(ctx: RunContext[TurnDeps]) -> str:
        """Show the remembered IANA timezone used for reminders."""
        return await traced(
            ctx, "timezone_get", {}, lambda: _timezone_get(home=ctx.deps.settings.home)
        )

    return {"timezone_get": timezone_get}
