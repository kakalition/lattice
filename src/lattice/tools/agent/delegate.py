"""Agent tool: delegate."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def delegate(ctx: RunContext[TurnDeps], task: str, context: str = "") -> str:
        """Delegate a bounded subtask to the secondary worker (same tools as you, minus delegate).
        Use for research/lookup/analysis you do not need to do inline; synthesize the result yourself."""
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

    return {"delegate": delegate}
