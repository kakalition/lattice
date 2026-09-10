"""Agent tool: web_fetch."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.web import web_fetch


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def web_fetch_tool(ctx: RunContext[TurnDeps], url: str) -> str:
        if err := not_allowed(ctx, "web_fetch"):
            return err
        return await traced(ctx, "web_fetch", {"url": url}, lambda: web_fetch(url))

    return {"web_fetch": web_fetch_tool}
