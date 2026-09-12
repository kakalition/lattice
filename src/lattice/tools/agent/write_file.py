"""Agent tool: write_file."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, maybe_approve, traced
from lattice.tools.agent._common import ToolTier, ToolsetT
from lattice.tools.file import write_file as _write_file

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def write_file(ctx: RunContext[TurnDeps], path: str, content: str) -> str:
        denied = await maybe_approve(ctx, "write_file", path, path=path)
        if denied:
            return denied
        return await traced(
            ctx,
            "write_file",
            {"path": path, "content": content},
            lambda: _write_file(
                path, content, workspace=ctx.deps.workspace, home=ctx.deps.settings.home
            ),
        )

    return {"write_file": write_file}
