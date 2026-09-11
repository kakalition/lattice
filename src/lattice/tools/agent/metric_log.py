"""Agent tool: metric_log."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.metrics import metric_log


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def metric_log_tool(
        ctx: RunContext[TurnDeps],
        name: str,
        value: float,
        unit: str | None = None,
        tags: str | None = None,
        note: str | None = None,
        at: str | None = None,
    ) -> str:
        """Record a personal metric point (habit, focus hours, mood, reps, …)."""
        if err := not_allowed(ctx, "metric_log"):
            return err
        return await traced(
            ctx,
            "metric_log",
            {"name": name, "value": value, "unit": unit, "at": at},
            lambda: metric_log(
                name,
                value,
                unit=unit,
                tags=tags,
                note=note,
                at=at,
                home=ctx.deps.settings.home,
            ),
        )

    return {"metric_log": metric_log_tool}
