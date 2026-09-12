"""Agent tool: sqlite_query."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.sqlite import sqlite_query as _sqlite_query
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def sqlite_query(ctx: RunContext[TurnDeps], name: str, sql: str) -> str:
        return await traced(
            ctx,
            "sqlite_query",
            {"name": name, "sql": sql},
            lambda: _sqlite_query(
                ctx.deps.sqlite_pool,
                name,
                sql,
                allow=ctx.deps.profile.sqlite_allow,
                row_limit=ctx.deps.settings.sqlite.query_row_limit,
            ),
        )

    return {"sqlite_query": sqlite_query}
