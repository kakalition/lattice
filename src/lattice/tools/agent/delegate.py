"""Agent tool: delegate."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def delegate_tool(ctx: RunContext[TurnDeps], task: str, context: str = "") -> str:
        """Delegate a bounded research/analysis task to the secondary worker model.
        Use for web/file/sqlite lookups; synthesize the result yourself for the user."""
        if err := not_allowed(ctx, "delegate"):
            return err
        if ctx.deps.delegate_depth > 0:
            return "error: nested delegate is not allowed"
        from lattice.agents.secondary import run_secondary
        from lattice.providers.settings import secondary_model_name

        mid = secondary_model_name(
            ctx.deps.settings, profile_secondary=ctx.deps.profile.secondary_model
        )
        return await traced(
            ctx,
            "delegate",
            {"task": task, "context": context, "secondary_model": mid},
            lambda: run_secondary(ctx.deps, task=task, context=context),
        )

    return {"delegate": delegate_tool}
