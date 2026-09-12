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
    async def read_file(ctx: RunContext[TurnDeps], path: str) -> str:
        home = ctx.deps.settings.home
        session_id = ctx.deps.session_id

        async def _op() -> str:
            target = resolve_agent_path(path, ctx.deps.workspace, home=home)
            stat = await asyncio.to_thread(target.stat)
            if read_cache.is_unchanged(session_id, str(target), stat.st_mtime_ns, stat.st_size):
                return f"[unchanged since last read: {path} ({stat.st_size} bytes)]"
            text = await _read_file(path, workspace=ctx.deps.workspace, home=home)
            fresh = await asyncio.to_thread(target.stat)
            read_cache.record_read(session_id, str(target), fresh.st_mtime_ns, fresh.st_size)
            return text

        return await traced(ctx, "read_file", {"path": path}, _op)

    return {"read_file": read_file}
