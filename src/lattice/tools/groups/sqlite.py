"""Built-in ``sqlite`` group — named database administration."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.sqlite import sqlite_backup as _sqlite_backup
from lattice.sqlite import sqlite_execute as _sqlite_execute
from lattice.sqlite import sqlite_list as _sqlite_list
from lattice.sqlite import sqlite_query as _sqlite_query
from lattice.sqlite import sqlite_register as _sqlite_register
from lattice.sqlite import sqlite_schema as _sqlite_schema
from lattice.sqlite import sqlite_unregister as _sqlite_unregister
from lattice.tools.groups._common import ToolBinding, ToolGroup, ToolsetT
from lattice.tools.middleware import ToolPolicy

_GROUP = "sqlite"

_EXECUTE_POLICY = ToolPolicy(
    summary=lambda _ctx, args: f"{args.get('name', '')}: {str(args.get('sql', ''))[:120]}"
)
_REGISTER_POLICY = ToolPolicy(
    summary=lambda _ctx, args: f"{args.get('name', '')}->{args.get('path', '')}"
)
_UNREGISTER_POLICY = ToolPolicy(summary=lambda _ctx, args: str(args.get("name") or ""))


def register_list(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def list(ctx: RunContext[TurnDeps]) -> str:
        return await _sqlite_list(ctx.deps.sqlite_registry, ctx.deps.profile.sqlite_allow)

    return {"list": list}


def register_schema(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def schema(ctx: RunContext[TurnDeps], name: str) -> str:
        return await _sqlite_schema(ctx.deps.sqlite_pool, name, ctx.deps.profile.sqlite_allow)

    return {"schema": schema}


def register_query(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def query(ctx: RunContext[TurnDeps], name: str, sql: str) -> str:
        return await _sqlite_query(
            ctx.deps.sqlite_pool,
            name,
            sql,
            allow=ctx.deps.profile.sqlite_allow,
            row_limit=ctx.deps.settings.sqlite.query_row_limit,
        )

    return {"query": query}


def register_execute(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def execute(ctx: RunContext[TurnDeps], name: str, sql: str, dry_run: bool = False) -> str:
        """Run write SQL (DDL/DML) against a named database.

        Pass several statements separated by ``;`` in one call to batch bulk writes
        into a single transaction — far faster than one call per row. Prefer an
        INTEGER PRIMARY KEY for rowid-optimized storage, and add indexes only on
        columns you actually filter by (extra indexes slow writes down).
        """
        return await _sqlite_execute(
            ctx.deps.sqlite_pool,
            name,
            sql,
            allow=ctx.deps.profile.sqlite_allow,
            dry_run=dry_run,
        )

    return {"execute": execute}


def register_register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def register(
        ctx: RunContext[TurnDeps], name: str, path: str, read_only: bool = False
    ) -> str:
        return await _sqlite_register(ctx.deps.sqlite_registry, name, path, read_only=read_only)

    return {"register": register}


def register_unregister(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def unregister(ctx: RunContext[TurnDeps], name: str) -> str:
        return await _sqlite_unregister(ctx.deps.sqlite_registry, name)

    return {"unregister": unregister}


def register_backup(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def backup(ctx: RunContext[TurnDeps], name: str) -> str:
        return await _sqlite_backup(ctx.deps.sqlite_registry, name, ctx.deps.profile.sqlite_allow)

    return {"backup": backup}


GROUP = ToolGroup(
    name=_GROUP,
    description="SQLite: list/schema/query/execute/register/unregister/backup named databases.",
    bindings=(
        ToolBinding("list", ToolTier.COLD, register_list),
        ToolBinding("schema", ToolTier.EAGER, register_schema),
        ToolBinding("query", ToolTier.EAGER, register_query),
        ToolBinding("execute", ToolTier.COLD, register_execute, _EXECUTE_POLICY),
        ToolBinding("register", ToolTier.COLD, register_register, _REGISTER_POLICY),
        ToolBinding("unregister", ToolTier.COLD, register_unregister, _UNREGISTER_POLICY),
        ToolBinding("backup", ToolTier.COLD, register_backup),
    ),
)
