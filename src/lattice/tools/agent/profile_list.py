"""Agent tool: profile_list."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.profiles.store import list_profiles
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def profile_list_tool(ctx: RunContext[TurnDeps]) -> str:
        if err := not_allowed(ctx, "profile_list"):
            return err

        def _op() -> str:
            names = list_profiles(ctx.deps.settings.home)
            return "\n".join(names) if names else "(no profiles)"

        return await traced(ctx, "profile_list", {}, _op)

    return {"profile_list": profile_list_tool}
