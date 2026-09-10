"""Agent tool: sqlite_query."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.sqlite import sqlite_query
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def sqlite_query_tool(ctx: RunContext[TurnDeps], name: str, sql: str) -> str:
        if err := not_allowed(ctx, "sqlite_query"):
            return err
        return await traced(
            ctx,
            "sqlite_query",
            {"name": name, "sql": sql},
            lambda: sqlite_query(
                ctx.deps.sqlite_pool,
                name,
                sql,
                allow=ctx.deps.profile.sqlite_allow,
                row_limit=ctx.deps.settings.sqlite.query_row_limit,
            ),
        )

    return {"sqlite_query": sqlite_query_tool}
