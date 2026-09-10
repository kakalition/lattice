"""Agent tool: skill_view."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.skills import skill_view
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def skill_view_tool(ctx: RunContext[TurnDeps], name: str) -> str:
        if err := not_allowed(ctx, "skill_view"):
            return err
        return await traced(
            ctx, "skill_view", {"name": name}, lambda: skill_view(name, ctx.deps.skills)
        )

    return {"skill_view": skill_view_tool}
