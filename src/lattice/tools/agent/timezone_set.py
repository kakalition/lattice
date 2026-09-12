"""Agent tool: timezone_set."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.scheduler.tools import timezone_set as _timezone_set
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def timezone_set(ctx: RunContext[TurnDeps], timezone: str) -> str:
        """Persist the user's IANA timezone (e.g. Asia/Ho_Chi_Minh) for all reminders."""

        def _set() -> str:
            result = _timezone_set(timezone, home=ctx.deps.settings.home)
            if result.startswith("timezone saved:"):
                ctx.deps.settings.timezone = timezone.strip()
            return result

        return await traced(ctx, "timezone_set", {"timezone": timezone}, _set)

    return {"timezone_set": timezone_set}
