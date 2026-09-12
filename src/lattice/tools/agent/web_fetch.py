"""Agent tool: web_fetch."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolTier, ToolsetT
from lattice.tools.web import web_fetch as _web_fetch

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def web_fetch(ctx: RunContext[TurnDeps], url: str) -> str:
        return await traced(ctx, "web_fetch", {"url": url}, lambda: _web_fetch(url))

    return {"web_fetch": web_fetch}
