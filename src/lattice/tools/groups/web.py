"""Built-in ``web`` group — search and fetch."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.tools.groups._common import ToolBinding, ToolGroup, ToolsetT
from lattice.tools.web import web_fetch as _web_fetch
from lattice.tools.web import web_search as _web_search

_GROUP = "web"


def register_search(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def search(ctx: RunContext[TurnDeps], query: str) -> str:
        return await _web_search(query, api_key=ctx.deps.settings.tavily_api_key)

    return {"search": search}


def register_fetch(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def fetch(ctx: RunContext[TurnDeps], url: str) -> str:
        return await _web_fetch(url)

    return {"fetch": fetch}


GROUP = ToolGroup(
    name=_GROUP,
    description="Web: search for pages and fetch static HTML.",
    bindings=(
        ToolBinding("search", ToolTier.EAGER, register_search),
        ToolBinding("fetch", ToolTier.EAGER, register_fetch),
    ),
)
