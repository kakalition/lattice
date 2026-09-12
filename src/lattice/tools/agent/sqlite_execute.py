"""Agent tool: sqlite_execute."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, maybe_approve, traced
from lattice.sqlite import sqlite_execute as _sqlite_execute
from lattice.tools.agent._common import ToolTier, ToolsetT

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def sqlite_execute(
        ctx: RunContext[TurnDeps], name: str, sql: str, dry_run: bool = False
    ) -> str:
        """Run write SQL (DDL/DML) against a named database.

        Pass several statements separated by ``;`` in one call to batch bulk writes
        into a single transaction — far faster than one call per row. Prefer an
        INTEGER PRIMARY KEY for rowid-optimized storage, and add indexes only on
        columns you actually filter by (extra indexes slow writes down).
        """
        denied = await maybe_approve(
            ctx, "sqlite_execute", f"{name}: {sql[:120]}", name=name, sql=sql
        )
        if denied:
            return denied
        return await traced(
            ctx,
            "sqlite_execute",
            {"name": name, "sql": sql, "dry_run": dry_run},
            lambda: _sqlite_execute(
                ctx.deps.sqlite_pool,
                name,
                sql,
                allow=ctx.deps.profile.sqlite_allow,
                dry_run=dry_run,
            ),
        )

    return {"sqlite_execute": sqlite_execute}
