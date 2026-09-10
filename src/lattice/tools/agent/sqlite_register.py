"""Agent tool: sqlite_register."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, maybe_approve, traced
from lattice.sqlite import sqlite_register
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def sqlite_register_tool(
        ctx: RunContext[TurnDeps], name: str, path: str, read_only: bool = False
    ) -> str:
        if err := not_allowed(ctx, "sqlite_register"):
            return err
        denied = await maybe_approve(
            ctx, "sqlite_register", f"{name}->{path}", name=name, path=path
        )
        if denied:
            return denied
        return await traced(
            ctx,
            "sqlite_register",
            {"name": name, "path": path, "read_only": read_only},
            lambda: sqlite_register(ctx.deps.sqlite_registry, name, path, read_only=read_only),
        )

    return {"sqlite_register": sqlite_register_tool}
