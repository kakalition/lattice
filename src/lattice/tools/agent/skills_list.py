"""Agent tool: skills_list."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.skills import skills_list as _skills_list
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def skills_list(ctx: RunContext[TurnDeps]) -> str:
        return await traced(
            ctx,
            "skills_list",
            {},
            lambda: _skills_list(
                ctx.deps.skills,
                prefer=ctx.deps.profile.skills_prefer,
                disable=ctx.deps.profile.skills_disable,
            ),
        )

    return {"skills_list": skills_list}
