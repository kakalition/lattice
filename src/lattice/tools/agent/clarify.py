"""Agent tool: clarify."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.clarify import clarify as clarify_impl


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def clarify(
        ctx: RunContext[TurnDeps], question: str, choices: list[str] | None = None
    ) -> str:
        if err := not_allowed(ctx, "clarify"):
            return err
        return await traced(
            ctx,
            "clarify",
            {"question": question, "choices": choices},
            lambda: clarify_impl(question, ctx.deps.hitl, choices=choices),
        )

    return {"clarify": clarify}
