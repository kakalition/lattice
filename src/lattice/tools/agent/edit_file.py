"""Agent tool: edit_file."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolsetT, ToolTier
from lattice.tools.file import edit_file as _edit_file

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def edit_file(
        ctx: RunContext[TurnDeps], path: str, old_string: str, new_string: str
    ) -> str:
        return await traced(
            ctx,
            "edit_file",
            {"path": path, "old_string": old_string, "new_string": new_string},
            lambda: _edit_file(
                path,
                old_string,
                new_string,
                workspace=ctx.deps.workspace,
                home=ctx.deps.settings.home,
            ),
        )

    return {"edit_file": edit_file}
