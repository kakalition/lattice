"""Agent tool: skill_view."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.skills import scan_skills_for
from lattice.skills import skill_view as _skill_view
from lattice.tools.agent._common import ToolsetT, ToolTier

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def skill_view(ctx: RunContext[TurnDeps], name: str) -> str:
        def _op() -> str:
            report = scan_skills_for(ctx.deps.settings.home, ctx.deps.profile)
            return _skill_view(name, report.skills)

        return await traced(ctx, "skill_view", {"name": name}, _op)

    return {"skill_view": skill_view}
