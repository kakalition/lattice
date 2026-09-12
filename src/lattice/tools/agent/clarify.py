"""Agent tool: clarify."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolTier, ToolsetT
from lattice.tools.clarify import clarify as _clarify

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def clarify(
        ctx: RunContext[TurnDeps], question: str, choices: list[str] | None = None
    ) -> str:
        return await traced(
            ctx,
            "clarify",
            {"question": question, "choices": choices},
            lambda: _clarify(question, ctx.deps.hitl, choices=choices),
        )

    return {"clarify": clarify}
