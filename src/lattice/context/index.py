"""Bounded workspace + database schema index for the volatile notice.

Cheap, read-only discovery injected ahead of the user turn so the model can
skip its own ``ls``/``find``/``sqlite_schema`` warm-up calls. Everything is
clipped and capped; a failure here only means a shorter notice.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

_SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        ".run",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)
_MAX_DIRS = 12
_MAX_FILES = 20
_MAX_DBS = 6
_MAX_TABLES = 12
_MAX_COLS = 10
_MAX_LINE = 500

# DB schema is stable between writes; cache per path keyed by (mtime_ns, size).
_schema_cache: OrderedDict[str, tuple[int, int, str]] = OrderedDict()
_schema_lock = threading.Lock()
_SCHEMA_CACHE_MAX = 32

# Directory listing is rescanned every turn; cache it keyed by directory
# (mtime_ns, size) with a short TTL so a file edit inside an existing entry
# still refreshes the "recent files" ordering without rescanning per turn.
_workspace_cache: OrderedDict[str, tuple[int, int, float, str]] = OrderedDict()
_workspace_lock = threading.Lock()
_WORKSPACE_CACHE_MAX = 16
_WORKSPACE_TTL_SECONDS = 2.0


def reset_workspace_cache() -> None:
    with _workspace_lock:
        _workspace_cache.clear()


def workspace_index(workspace: Path) -> str:
    """One-level listing: dirs plus most-recently-modified files."""
    key = str(workspace)
    try:
        stat = workspace.stat()
        fingerprint = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return ""
    now = time.monotonic()
    with _workspace_lock:
        cached = _workspace_cache.get(key)
        if (
            cached is not None
            and cached[:2] == fingerprint
            and (now - cached[2]) < _WORKSPACE_TTL_SECONDS
        ):
            _workspace_cache.move_to_end(key)
            return cached[3]
    try:
        entries = list(os.scandir(workspace))
    except OSError:
        return ""
    dirs: list[str] = []
    files: list[tuple[float, str]] = []
    for entry in entries:
        name = entry.name
        if name.startswith("."):
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                if name not in _SKIP_DIRS:
                    dirs.append(name + "/")
            elif entry.is_file(follow_symlinks=False):
                files.append((entry.stat().st_mtime, name))
        except OSError:
            continue
    dirs.sort()
    files.sort(reverse=True)
    lines: list[str] = []
    if dirs:
        shown = ", ".join(dirs[:_MAX_DIRS]) + ("…" if len(dirs) > _MAX_DIRS else "")
        lines.append(f"Dirs: {shown}")
    if files:
        names = ", ".join(name for _, name in files[:_MAX_FILES])
        lines.append(f"Recent files: {names}" + ("…" if len(files) > _MAX_FILES else ""))
    text = "\n".join(lines)
    with _workspace_lock:
        _workspace_cache[key] = (fingerprint[0], fingerprint[1], now, text)
        _workspace_cache.move_to_end(key)
        while len(_workspace_cache) > _WORKSPACE_CACHE_MAX:
            _workspace_cache.popitem(last=False)
    return text


def _quote(table: str) -> str:
    return '"' + table.replace('"', '""') + '"'


async def _introspect(entry: Any, pool: Any, allow: list[str] | None) -> str:
    try:
        _, conn = await pool.get(entry.name, allow=allow)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [row[0] for row in await cur.fetchall()]
        parts: list[str] = []
        for table in tables[:_MAX_TABLES]:
            cur = await conn.execute(f"PRAGMA table_info({_quote(table)})")
            cols = [row[1] for row in await cur.fetchall()]
            shown = ", ".join(cols[:_MAX_COLS]) + ("…" if len(cols) > _MAX_COLS else "")
            parts.append(f"{table}({shown})")
        return (f"DB {entry.name}: " + "; ".join(parts))[:_MAX_LINE] if parts else ""
    except Exception:
        return ""


async def database_schema_lines(
    registry: Any, pool: Any, *, allow: list[str] | None = None
) -> list[str]:
    """Compact ``table(col, col)`` lines for each registered database.

    Cached per database file, invalidated by ``(mtime_ns, size)``.
    """
    lines: list[str] = []
    for entry in registry.list(allow)[:_MAX_DBS]:
        try:
            stat = await asyncio.to_thread(entry.path.stat)
            fingerprint = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            fingerprint = (0, 0)
        key = str(entry.path)
        with _schema_lock:
            cached = _schema_cache.get(key)
            if cached is not None and cached[:2] == fingerprint:
                _schema_cache.move_to_end(key)
                if cached[2]:
                    lines.append(cached[2])
                continue
        line = await _introspect(entry, pool, allow)
        # Re-stat after introspection: opening a connection can touch the file,
        # so store the settled fingerprint or the next call would always miss.
        try:
            fresh = await asyncio.to_thread(entry.path.stat)
            settled = (fresh.st_mtime_ns, fresh.st_size)
        except OSError:
            settled = fingerprint
        with _schema_lock:
            _schema_cache[key] = (settled[0], settled[1], line)
            _schema_cache.move_to_end(key)
            while len(_schema_cache) > _SCHEMA_CACHE_MAX:
                _schema_cache.popitem(last=False)
        if line:
            lines.append(line)
    return lines


async def build_workspace_context(
    workspace: Path,
    registry: Any,
    pool: Any,
    *,
    allow: list[str] | None = None,
) -> str:
    """Combined workspace index + DB schemas, or ``""`` when nothing to say."""
    blocks: list[str] = []
    index = await asyncio.to_thread(workspace_index, workspace)
    if index:
        blocks.append("Workspace index:\n" + index)
    schemas = await database_schema_lines(registry, pool, allow=allow)
    if schemas:
        blocks.append("DB schemas:\n" + "\n".join(schemas))
    return "\n".join(blocks)
