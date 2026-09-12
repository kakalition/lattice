"""Agent tool: calculator."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolsetT, ToolTier
from lattice.tools.calculator import calculate

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def calculator(ctx: RunContext[TurnDeps], expression: str) -> str:
        """Evaluate a math expression safely (arithmetic, parentheses, powers,
        and functions like sqrt/sin/log). Example: "(3 + 4) * 2"."""
        return await traced(
            ctx, "calculator", {"expression": expression}, lambda: calculate(expression)
        )

    return {"calculator": calculator}
