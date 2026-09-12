"""Agent tool: skill_view."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.skills import skill_view as _skill_view
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def skill_view(ctx: RunContext[TurnDeps], name: str) -> str:
        return await traced(
            ctx, "skill_view", {"name": name}, lambda: _skill_view(name, ctx.deps.skills)
        )

    return {"skill_view": skill_view}
