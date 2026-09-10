"""aiosqlite connection pool/handles per named DB."""

from __future__ import annotations

import aiosqlite

from lattice.sqlite.registry import DbEntry, SqliteRegistry


class SqlitePool:
    def __init__(self, registry: SqliteRegistry) -> None:
        self.registry = registry
        self._conns: dict[str, aiosqlite.Connection] = {}

    async def get(
        self, name: str, allow: list[str] | None = None
    ) -> tuple[DbEntry, aiosqlite.Connection]:
        entry = self.registry.get(name, allow=allow)
        if name not in self._conns:
            conn = await aiosqlite.connect(entry.path)
            await conn.execute("PRAGMA journal_mode=WAL")
            self._conns[name] = conn
        return entry, self._conns[name]

    async def close_all(self) -> None:
        for conn in self._conns.values():
            await conn.close()
        self._conns.clear()
