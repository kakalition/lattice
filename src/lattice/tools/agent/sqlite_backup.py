"""Agent tool: sqlite_backup."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.sqlite import sqlite_backup
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def sqlite_backup_tool(ctx: RunContext[TurnDeps], name: str) -> str:
        if err := not_allowed(ctx, "sqlite_backup"):
            return err
        return await traced(
            ctx,
            "sqlite_backup",
            {"name": name},
            lambda: sqlite_backup(ctx.deps.sqlite_registry, name, ctx.deps.profile.sqlite_allow),
        )

    return {"sqlite_backup": sqlite_backup_tool}
