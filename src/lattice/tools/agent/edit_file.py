"""Agent tool: edit_file."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, maybe_approve, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.file import edit_file


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def edit_file_tool(
        ctx: RunContext[TurnDeps], path: str, old_string: str, new_string: str
    ) -> str:
        if err := not_allowed(ctx, "edit_file"):
            return err
        denied = await maybe_approve(ctx, "edit_file", path, path=path)
        if denied:
            return denied
        return await traced(
            ctx,
            "edit_file",
            {"path": path, "old_string": old_string, "new_string": new_string},
            lambda: edit_file(
                path,
                old_string,
                new_string,
                workspace=ctx.deps.workspace,
                home=ctx.deps.settings.home,
            ),
        )

    return {"edit_file": edit_file_tool}
