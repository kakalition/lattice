"""Agent tool: read_file."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools import read_cache
from lattice.tools.agent._common import ToolsetT, ToolTier
from lattice.tools.file import read_file as _read_file
from lattice.tools.file_safety import resolve_agent_path

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def read_file(
        ctx: RunContext[TurnDeps], path: str, offset: int = 0, limit: int = 0
    ) -> str:
        """Read a file; optional ``offset``/``limit`` select a line range.

        ``offset`` is a 0-based line index and ``limit`` a line count (0 = to the
        end). With neither set, the whole (bounded) file is returned.
        """
        home = ctx.deps.settings.home
        # Scope by turn: tool results are not replayed, so a body served on a
        # previous turn is no longer in context and must be re-served.
        scope = ctx.deps.session_id
        if ctx.deps.turn_id:
            scope = f"{scope}:{ctx.deps.turn_id}"
        ranged = bool(offset or limit)

        async def _op() -> str:
            target = resolve_agent_path(path, ctx.deps.workspace, home=home)
            # The cache identity must include the range, or a ranged read would
            # elide a later full read of the same unchanged file.
            cache_path = str(target) if not ranged else f"{target}#offset={offset}&limit={limit}"
            stat = await asyncio.to_thread(target.stat)
            if read_cache.is_unchanged(scope, cache_path, stat.st_mtime_ns, stat.st_size):
                label = path if not ranged else f"{path} (offset={offset}, limit={limit})"
                return f"[unchanged since last read: {label} ({stat.st_size} bytes)]"
            text = await _read_file(
                path, workspace=ctx.deps.workspace, home=home, offset=offset, limit=limit
            )
            fresh = await asyncio.to_thread(target.stat)
            read_cache.record_read(scope, cache_path, fresh.st_mtime_ns, fresh.st_size)
            return text

        return await traced(ctx, "read_file", {"path": path, "offset": offset, "limit": limit}, _op)

    return {"read_file": read_file}
