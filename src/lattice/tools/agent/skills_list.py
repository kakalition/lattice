"""Agent tool: skills_list."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.skills import skills_list
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def skills_list_tool(ctx: RunContext[TurnDeps]) -> str:
        if err := not_allowed(ctx, "skills_list"):
            return err
        return await traced(
            ctx,
            "skills_list",
            {},
            lambda: skills_list(
                ctx.deps.skills,
                prefer=ctx.deps.profile.skills_prefer,
                disable=ctx.deps.profile.skills_disable,
            ),
        )

    return {"skills_list": skills_list_tool}
