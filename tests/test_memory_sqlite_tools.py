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
async def test_memory_add_stores_verbatim() -> None:
    """Explicit memory_add must not depend on mem0's extraction LLM."""
    captured: dict = {}

    class _Fake:
        async def add(self, text, *, metadata=None, infer=True):  # type: ignore[no-untyped-def]
            captured["text"] = text
            captured["infer"] = infer
            return "id"

    out = await memory_add(_Fake(), "User prefers decaf")  # type: ignore[arg-type]
    assert captured["text"] == "User prefers decaf"
    assert captured["infer"] is False
    assert "added memory id" in out


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


@pytest.mark.asyncio
async def test_sqlite_pragmas_applied_on_connect(tmp_path: Path) -> None:
    from lattice.sqlite.pragmas import read_pragmas

    settings = LatticeSettings(home=tmp_path)
    reg = SqliteRegistry(settings)
    reg.register("perf", tmp_path / "perf.db")
    pool = SqlitePool(reg)
    _, conn = await pool.get("perf")
    pragmas = await read_pragmas(conn)
    assert pragmas["journal_mode"] == "wal"
    assert pragmas["synchronous"] == "1"  # NORMAL
    assert pragmas["temp_store"] == "2"  # MEMORY
    assert int(pragmas["busy_timeout"]) == 5000
    assert int(pragmas["mmap_size"]) > 0
    await pool.close_all()


def test_split_statements_respects_literals_and_comments() -> None:
    from lattice.sqlite.tools import _split_statements

    assert _split_statements("INSERT INTO t VALUES ('a;b'); INSERT INTO t VALUES (2)") == [
        "INSERT INTO t VALUES ('a;b')",
        "INSERT INTO t VALUES (2)",
    ]
    assert _split_statements("-- c;omment\nSELECT 1; /* b; */ SELECT 2") == [
        "-- c;omment\nSELECT 1",
        "/* b; */ SELECT 2",
    ]
    assert _split_statements("SELECT 1") == ["SELECT 1"]


@pytest.mark.asyncio
async def test_sqlite_execute_batches_in_one_transaction(tmp_path: Path) -> None:
    settings = LatticeSettings(home=tmp_path)
    reg = SqliteRegistry(settings)
    reg.register("bulk", tmp_path / "bulk.db")
    pool = SqlitePool(reg)
    await sqlite_execute(pool, "bulk", "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    out = await sqlite_execute(
        pool,
        "bulk",
        "INSERT INTO t (v) VALUES ('a');INSERT INTO t (v) VALUES ('b');INSERT INTO t (v) VALUES ('c')",
    )
    assert "1 transaction" in out
    assert (await sqlite_query(pool, "bulk", "SELECT COUNT(*) FROM t")).strip().endswith("3")
    # A failing statement rolls the whole batch back — no partial writes.
    import sqlite3

    with pytest.raises(sqlite3.OperationalError):
        await sqlite_execute(
            pool, "bulk", "INSERT INTO t (v) VALUES ('d'); INSERT INTO missing (x) VALUES (1)"
        )
    assert (await sqlite_query(pool, "bulk", "SELECT COUNT(*) FROM t")).strip().endswith("3")
    await pool.close_all()
