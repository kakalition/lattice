"""Agent tool: sqlite_unregister."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, maybe_approve, traced
from lattice.sqlite import sqlite_unregister as _sqlite_unregister
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def sqlite_unregister(ctx: RunContext[TurnDeps], name: str) -> str:
        denied = await maybe_approve(ctx, "sqlite_unregister", name, name=name)
        if denied:
            return denied
        return await traced(
            ctx,
            "sqlite_unregister",
            {"name": name},
            lambda: _sqlite_unregister(ctx.deps.sqlite_registry, name),
        )

    return {"sqlite_unregister": sqlite_unregister}
