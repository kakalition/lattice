"""Built-in ``skills`` group — discover and load skills."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.skills import scan_skills_for
from lattice.skills import skill_view as _skill_view
from lattice.skills import skills_list as _skills_list
from lattice.tools.groups._common import ToolBinding, ToolGroup, ToolsetT

_GROUP = "skills"


def register_list(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def list(ctx: RunContext[TurnDeps]) -> str:
        report = scan_skills_for(ctx.deps.settings.home, ctx.deps.profile)
        return _skills_list(
            report.skills,
            prefer=ctx.deps.profile.skills_prefer,
            disable=ctx.deps.profile.skills_disable,
        )

    return {"list": list}


def register_view(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def view(ctx: RunContext[TurnDeps], name: str) -> str:
        report = scan_skills_for(ctx.deps.settings.home, ctx.deps.profile)
        return _skill_view(name, report.skills)

    return {"view": view}


GROUP = ToolGroup(
    name=_GROUP,
    description="Skills: list available skills and view a skill body.",
    bindings=(
        ToolBinding("list", ToolTier.EAGER, register_list),
        ToolBinding("view", ToolTier.EAGER, register_view),
    ),
)
