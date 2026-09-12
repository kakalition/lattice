"""Agent tool: metric_log."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolTier, ToolsetT
from lattice.tools.metrics import metric_log as _metric_log

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def metric_log(
        ctx: RunContext[TurnDeps],
        name: str,
        value: float,
        unit: str | None = None,
        tags: str | None = None,
        note: str | None = None,
        at: str | None = None,
    ) -> str:
        """Record a personal metric point (habit, focus hours, mood, reps, …)."""
        return await traced(
            ctx,
            "metric_log",
            {"name": name, "value": value, "unit": unit, "at": at},
            lambda: _metric_log(
                name,
                value,
                unit=unit,
                tags=tags,
                note=note,
                at=at,
                home=ctx.deps.settings.home,
            ),
        )

    return {"metric_log": metric_log}
