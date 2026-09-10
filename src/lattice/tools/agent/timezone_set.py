"""Agent tool: timezone_set."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.scheduler.tools import timezone_set
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def timezone_set_tool(ctx: RunContext[TurnDeps], timezone: str) -> str:
        """Persist the user's IANA timezone (e.g. Asia/Ho_Chi_Minh) for all reminders."""
        if err := not_allowed(ctx, "timezone_set"):
            return err

        def _set() -> str:
            result = timezone_set(timezone, home=ctx.deps.settings.home)
            if result.startswith("timezone saved:"):
                ctx.deps.settings.timezone = timezone.strip()
            return result

        return await traced(ctx, "timezone_set", {"timezone": timezone}, _set)

    return {"timezone_set": timezone_set_tool}
