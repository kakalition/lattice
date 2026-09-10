"""SQLite manager tools."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime

from lattice.paths import lattice_home
from lattice.sqlite.pool import SqlitePool
from lattice.sqlite.registry import SqliteRegistry

_WRITE_RE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER|ATTACH|DETACH|VACUUM|PRAGMA)\b",
    re.I,
)


async def sqlite_list(registry: SqliteRegistry, allow: list[str] | None = None) -> str:
    rows = registry.list(allow)
    if not rows:
        return "(no registered databases)"
    return "\n".join(f"{d.name}: {d.path} read_only={d.read_only}" for d in rows)


async def sqlite_schema(pool: SqlitePool, name: str, allow: list[str] | None = None) -> str:
    _, conn = await pool.get(name, allow=allow)
    cur = await conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
    )
    rows = await cur.fetchall()
    return "\n".join(f"{t} {n}: {sql}" for t, n, sql in rows) or "(empty schema)"


async def sqlite_query(
    pool: SqlitePool,
    name: str,
    sql: str,
    *,
    allow: list[str] | None = None,
    row_limit: int = 500,
) -> str:
    if _WRITE_RE.match(sql):
        raise ValueError("sqlite_query is read-only; use sqlite_execute for writes")
    _, conn = await pool.get(name, allow=allow)
    cur = await conn.execute(sql)
    rows = await cur.fetchmany(row_limit + 1)
    truncated = len(rows) > row_limit
    rows = rows[:row_limit]
    cols = [d[0] for d in cur.description] if cur.description else []
    lines = ["\t".join(cols)] if cols else []
    for row in rows:
        lines.append("\t".join("" if v is None else str(v) for v in row))
    text = "\n".join(lines)
    if truncated:
        text += "\n[truncated]"
    return text[:100_000]


async def sqlite_execute(
    pool: SqlitePool,
    name: str,
    sql: str,
    *,
    allow: list[str] | None = None,
    dry_run: bool = False,
) -> str:
    entry, conn = await pool.get(name, allow=allow)
    if entry.read_only:
        raise PermissionError(f"database {name} is read_only")
    if dry_run:
        return f"dry-run ok: {sql[:200]}"
    await conn.execute(sql)
    await conn.commit()
    return "ok"


async def sqlite_register(
    registry: SqliteRegistry, name: str, path: str, *, read_only: bool = False
) -> str:
    entry = registry.register(name, path, read_only=read_only)
    return f"registered {entry.name} -> {entry.path}"


async def sqlite_unregister(registry: SqliteRegistry, name: str) -> str:
    registry.unregister(name)
    return f"unregistered {name}"


async def sqlite_backup(registry: SqliteRegistry, name: str, allow: list[str] | None = None) -> str:
    entry = registry.get(name, allow=allow)
    backup_dir = lattice_home() / "sqlite" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"{name}-{stamp}.db"
    shutil.copy2(entry.path, dest)
    return str(dest)
