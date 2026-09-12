"""Agent tool: skills_list."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.skills import scan_skills_for
from lattice.skills import skills_list as _skills_list
from lattice.tools.agent._common import ToolsetT, ToolTier

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def skills_list(ctx: RunContext[TurnDeps]) -> str:
        def _op() -> str:
            report = scan_skills_for(ctx.deps.settings.home, ctx.deps.profile)
            return _skills_list(
                report.skills,
                prefer=ctx.deps.profile.skills_prefer,
                disable=ctx.deps.profile.skills_disable,
            )

        return await traced(ctx, "skills_list", {}, _op)

    return {"skills_list": skills_list}
