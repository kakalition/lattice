"""Agent tool: sqlite_schema."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.sqlite import sqlite_schema
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def sqlite_schema_tool(ctx: RunContext[TurnDeps], name: str) -> str:
        if err := not_allowed(ctx, "sqlite_schema"):
            return err
        return await traced(
            ctx,
            "sqlite_schema",
            {"name": name},
            lambda: sqlite_schema(ctx.deps.sqlite_pool, name, ctx.deps.profile.sqlite_allow),
        )

    return {"sqlite_schema": sqlite_schema_tool}
