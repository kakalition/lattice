"""Agent tool: sqlite_list."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.sqlite import sqlite_list as _sqlite_list
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def sqlite_list(ctx: RunContext[TurnDeps]) -> str:
        return await traced(
            ctx,
            "sqlite_list",
            {},
            lambda: _sqlite_list(ctx.deps.sqlite_registry, ctx.deps.profile.sqlite_allow),
        )

    return {"sqlite_list": sqlite_list}
