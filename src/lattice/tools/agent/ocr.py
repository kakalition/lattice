"""Agent tool: ocr."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolsetT, ToolTier
from lattice.tools.ocr import ocr_image

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def ocr(ctx: RunContext[TurnDeps], path: str) -> str:
        """Extract text from an image (png/jpg/webp/…). Use paths from [media] or workspace."""
        return await traced(
            ctx,
            "ocr",
            {"path": path},
            lambda: ocr_image(path, workspace=ctx.deps.workspace, home=ctx.deps.settings.home),
        )

    return {"ocr": ocr}
