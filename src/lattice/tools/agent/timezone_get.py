"""Agent tool: timezone_get."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.scheduler.tools import timezone_get
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def timezone_get_tool(ctx: RunContext[TurnDeps]) -> str:
        """Show the remembered IANA timezone used for reminders."""
        if err := not_allowed(ctx, "timezone_get"):
            return err
        return await traced(
            ctx, "timezone_get", {}, lambda: timezone_get(home=ctx.deps.settings.home)
        )

    return {"timezone_get": timezone_get_tool}
