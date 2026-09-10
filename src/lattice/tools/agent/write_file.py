"""Agent tool: write_file."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, maybe_approve, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.file import write_file


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def write_file_tool(ctx: RunContext[TurnDeps], path: str, content: str) -> str:
        if err := not_allowed(ctx, "write_file"):
            return err
        denied = await maybe_approve(ctx, "write_file", path, path=path)
        if denied:
            return denied
        return await traced(
            ctx,
            "write_file",
            {"path": path, "content": content},
            lambda: write_file(
                path, content, workspace=ctx.deps.workspace, home=ctx.deps.settings.home
            ),
        )

    return {"write_file": write_file_tool}
