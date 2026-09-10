"""Agent tool: profile_remove."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.audit import audit_log
from lattice.deps import TurnDeps, maybe_approve, traced
from lattice.profiles.store import remove_profile
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def profile_remove_tool(ctx: RunContext[TurnDeps], profile_id: str) -> str:
        if err := not_allowed(ctx, "profile_remove"):
            return err
        denied = await maybe_approve(
            ctx, "profile_remove", f"remove profile {profile_id}", profile_id=profile_id
        )
        if denied:
            return denied

        async def _op() -> str:
            try:
                remove_profile(profile_id, ctx.deps.settings.home)
            except (ValueError, FileNotFoundError) as exc:
                return f"error: {exc}"
            cleared = await ctx.deps.store.clear_sticky_for_profile(profile_id)
            audit_log(
                "tool",
                {"name": "profile_remove", "profile_id": profile_id, "sticky_cleared": cleared},
                home=ctx.deps.settings.home,
            )
            note = ""
            if ctx.deps.profile.id == profile_id:
                note = " (was active this turn — switch to another profile next message)"
            return f"removed profile {profile_id}; cleared {cleared} sticky mapping(s){note}"

        return await traced(ctx, "profile_remove", {"profile_id": profile_id}, _op)

    return {"profile_remove": profile_remove_tool}
