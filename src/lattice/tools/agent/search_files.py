"""Agent tool: search_files."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolsetT, ToolTier
from lattice.tools.file import search_files as _search_files

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def search_files(ctx: RunContext[TurnDeps], pattern: str, glob: str = "**/*") -> str:
        """Find a literal substring in workspace files (not a regex).

        Returns ``path:line: text`` for content matches; ``glob`` optionally
        narrows the scanned files (default ``**/*``).
        """
        return await traced(
            ctx,
            "search_files",
            {"pattern": pattern, "glob": glob},
            lambda: _search_files(pattern, workspace=ctx.deps.workspace, glob=glob),
        )

    return {"search_files": search_files}
