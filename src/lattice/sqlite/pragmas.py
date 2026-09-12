"""Shared SQLite connection PRAGMAs for local throughput and concurrency.

Every Lattice connection (named DBs, session state.db) is opened through
:func:`apply_perf_pragmas` so the tuning is identical everywhere and survives a
process restart rather than living in a one-off shell session.
"""

from __future__ import annotations

import aiosqlite

# WAL lets readers proceed during a write; NORMAL is durable enough under WAL and
# avoids an fsync per commit. Together these are the main write-throughput levers.
JOURNAL_MODE = "WAL"
SYNCHRONOUS = "NORMAL"
# Keep temp b-trees (sorts, GROUP BY, temp indices) in RAM instead of on disk.
TEMP_STORE = "MEMORY"
# Memory-map the database for faster reads. SQLite caps this at the file size, so a
# large value is a ceiling, not an allocation.
MMAP_SIZE_BYTES = 30_000_000_000
# Wait for a lock instead of raising "database is locked" immediately.
BUSY_TIMEOUT_MS = 5000


async def apply_perf_pragmas(
    conn: aiosqlite.Connection,
    *,
    mmap_size: int = MMAP_SIZE_BYTES,
    busy_timeout_ms: int = BUSY_TIMEOUT_MS,
) -> None:
    """Apply the standard performance PRAGMAs to a freshly opened connection.

    Called on every connect because these settings are per-connection; only
    ``journal_mode`` persists in the database file.
    """
    await conn.execute(f"PRAGMA journal_mode={JOURNAL_MODE}")
    await conn.execute(f"PRAGMA synchronous={SYNCHRONOUS}")
    await conn.execute(f"PRAGMA temp_store={TEMP_STORE}")
    await conn.execute(f"PRAGMA mmap_size={max(0, int(mmap_size))}")
    await conn.execute(f"PRAGMA busy_timeout={max(0, int(busy_timeout_ms))}")


async def read_pragmas(conn: aiosqlite.Connection) -> dict[str, str]:
    """Return the effective values of the tuning PRAGMAs (for doctor/debug output)."""
    out: dict[str, str] = {}
    for name in ("journal_mode", "synchronous", "temp_store", "mmap_size", "busy_timeout"):
        cur = await conn.execute(f"PRAGMA {name}")
        row = await cur.fetchone()
        out[name] = "" if row is None else str(row[0])
    return out
