"""Agent tool: read_file."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolTier, ToolsetT
from lattice.tools.file import read_file as _read_file

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def read_file(ctx: RunContext[TurnDeps], path: str) -> str:
        return await traced(
            ctx,
            "read_file",
            {"path": path},
            lambda: _read_file(path, workspace=ctx.deps.workspace, home=ctx.deps.settings.home),
        )

    return {"read_file": read_file}
