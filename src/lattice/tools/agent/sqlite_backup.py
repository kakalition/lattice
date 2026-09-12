"""Agent tool: sqlite_backup."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.sqlite import sqlite_backup as _sqlite_backup
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def sqlite_backup(ctx: RunContext[TurnDeps], name: str) -> str:
        return await traced(
            ctx,
            "sqlite_backup",
            {"name": name},
            lambda: _sqlite_backup(ctx.deps.sqlite_registry, name, ctx.deps.profile.sqlite_allow),
        )

    return {"sqlite_backup": sqlite_backup}
