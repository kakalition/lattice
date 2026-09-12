"""Agent tool: metric_query."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolTier, ToolsetT
from lattice.tools.metrics import metric_query as _metric_query

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def metric_query(
        ctx: RunContext[TurnDeps],
        name: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 200,
    ) -> str:
        """Query metrics; with name, includes avg/sum/streak and per-day totals."""
        return await traced(
            ctx,
            "metric_query",
            {"name": name, "since": since, "until": until, "limit": limit},
            lambda: _metric_query(
                name,
                since=since,
                until=until,
                limit=limit,
                home=ctx.deps.settings.home,
            ),
        )

    return {"metric_query": metric_query}
