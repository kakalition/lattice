"""Agent tool: ocr."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.ocr import ocr_image


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def ocr_tool(ctx: RunContext[TurnDeps], path: str) -> str:
        """Extract text from an image (png/jpg/webp/…). Use paths from [media] or workspace."""
        if err := not_allowed(ctx, "ocr"):
            return err
        return await traced(
            ctx,
            "ocr",
            {"path": path},
            lambda: ocr_image(path, workspace=ctx.deps.workspace, home=ctx.deps.settings.home),
        )

    return {"ocr": ocr_tool}
