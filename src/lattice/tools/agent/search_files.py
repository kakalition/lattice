"""Agent tool: search_files."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.file import search_files


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def search_files_tool(ctx: RunContext[TurnDeps], pattern: str, glob: str = "**/*") -> str:
        if err := not_allowed(ctx, "search_files"):
            return err
        return await traced(
            ctx,
            "search_files",
            {"pattern": pattern, "glob": glob},
            lambda: search_files(pattern, workspace=ctx.deps.workspace, glob=glob),
        )

    return {"search_files": search_files_tool}
