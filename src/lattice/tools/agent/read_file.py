"""Agent tool: read_file."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.file import read_file


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def read_file_tool(ctx: RunContext[TurnDeps], path: str) -> str:
        if err := not_allowed(ctx, "read_file"):
            return err
        return await traced(
            ctx,
            "read_file",
            {"path": path},
            lambda: read_file(path, workspace=ctx.deps.workspace, home=ctx.deps.settings.home),
        )

    return {"read_file": read_file_tool}
