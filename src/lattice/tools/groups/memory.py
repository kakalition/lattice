"""Built-in ``memory`` group — session and long-term memory."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.memory.tools import memory_add as _memory_add
from lattice.memory.tools import memory_forget as _memory_forget
from lattice.memory.tools import memory_search as _memory_search
from lattice.memory.tools import memory_update as _memory_update
from lattice.tools.groups._common import ToolBinding, ToolGroup, ToolsetT
from lattice.tools.session_search import session_search as _session_search

_GROUP = "memory"


def register_session_search(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def session_search(ctx: RunContext[TurnDeps], query: str) -> str:
        return await _session_search(query, ctx.deps.session)

    return {"session_search": session_search}


def register_search(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def search(ctx: RunContext[TurnDeps], query: str) -> str:
        return await _memory_search(ctx.deps.memory, query)

    return {"search": search}


def register_add(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def add(ctx: RunContext[TurnDeps], text: str) -> str:
        return await _memory_add(ctx.deps.memory, text)

    return {"add": add}


def register_update(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def update(ctx: RunContext[TurnDeps], memory_id: str, text: str) -> str:
        return await _memory_update(ctx.deps.memory, memory_id, text)

    return {"update": update}


def register_forget(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def forget(ctx: RunContext[TurnDeps], memory_id: str) -> str:
        return await _memory_forget(ctx.deps.memory, memory_id)

    return {"forget": forget}


GROUP = ToolGroup(
    name=_GROUP,
    description="Memory: search session history and the long-term store; add/update/forget.",
    bindings=(
        ToolBinding("session_search", ToolTier.EAGER, register_session_search),
        ToolBinding("search", ToolTier.EAGER, register_search),
        ToolBinding("add", ToolTier.EAGER, register_add),
        ToolBinding("update", ToolTier.COLD, register_update),
        ToolBinding("forget", ToolTier.COLD, register_forget),
    ),
)
