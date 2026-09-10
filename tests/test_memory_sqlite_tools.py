"""Memory + sqlite tool smoke tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice.config import LatticeSettings
from lattice.memory import InMemoryMemory, memory_add, memory_forget, memory_search
from lattice.sqlite import SqlitePool, SqliteRegistry, sqlite_execute, sqlite_list, sqlite_query


@pytest.mark.asyncio
async def test_memory_roundtrip() -> None:
    mem = InMemoryMemory("test")
    mid = await mem.add("user likes dark mode")
    hits = await memory_search(mem, "dark")
    assert hits and "dark" in hits
    await memory_forget(mem, mid)
    assert "no memories" in await memory_search(mem, "dark")
    assert "added memory" in await memory_add(mem, "again")


@pytest.mark.asyncio
async def test_sqlite_tools(tmp_path: Path) -> None:
    settings = LatticeSettings(home=tmp_path)
    reg = SqliteRegistry(settings)
    reg.register("notes", tmp_path / "notes.db")
    pool = SqlitePool(reg)
    listed = await sqlite_list(reg)
    assert "notes" in listed
    await sqlite_execute(pool, "notes", "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    await sqlite_execute(pool, "notes", "INSERT INTO t (v) VALUES ('hello')")
    rows = await sqlite_query(pool, "notes", "SELECT v FROM t")
    assert "hello" in rows
    with pytest.raises(ValueError):
        await sqlite_query(pool, "notes", "DELETE FROM t")
    await pool.close_all()
    # restart registry — still sees notes + data file
    reg2 = SqliteRegistry(LatticeSettings(home=tmp_path))
    assert any(d.name == "notes" for d in reg2.list())
    pool2 = SqlitePool(reg2)
    assert "hello" in await sqlite_query(pool2, "notes", "SELECT v FROM t")
    await pool2.close_all()
