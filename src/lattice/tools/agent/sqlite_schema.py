"""Agent tool: sqlite_schema."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.sqlite import sqlite_schema as _sqlite_schema
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def sqlite_schema(ctx: RunContext[TurnDeps], name: str) -> str:
        return await traced(
            ctx,
            "sqlite_schema",
            {"name": name},
            lambda: _sqlite_schema(ctx.deps.sqlite_pool, name, ctx.deps.profile.sqlite_allow),
        )

    return {"sqlite_schema": sqlite_schema}
