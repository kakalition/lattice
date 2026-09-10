"""Agent tool: sqlite_list."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.sqlite import sqlite_list
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def sqlite_list_tool(ctx: RunContext[TurnDeps]) -> str:
        if err := not_allowed(ctx, "sqlite_list"):
            return err
        return await traced(
            ctx,
            "sqlite_list",
            {},
            lambda: sqlite_list(ctx.deps.sqlite_registry, ctx.deps.profile.sqlite_allow),
        )

    return {"sqlite_list": sqlite_list_tool}
