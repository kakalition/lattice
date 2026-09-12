"""sessions.actions_json migration, bound, and round-trip."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from lattice.action_ledger import ActionRecord
from lattice.session import SessionStore


@pytest.mark.asyncio
async def test_actions_column_migrates_on_existing_db(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    conn = await aiosqlite.connect(db)
    await conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            parent_id TEXT,
            title TEXT,
            messages_json TEXT NOT NULL DEFAULT '[]',
            usage_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        "INSERT INTO sessions VALUES ('s1','default','u','cli',NULL,NULL,'[]','{}','n','n')"
    )
    await conn.commit()
    await conn.close()

    store = SessionStore(db)
    data = await store.get("s1")
    assert data is not None
    assert data["actions"] == []


@pytest.mark.asyncio
async def test_append_actions_bounded_and_round_trips(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state.db")
    sid = await store.create(profile_id="default", user_id="u", channel="cli")
    for i in range(25):
        await store.append_actions(sid, [ActionRecord(tool=f"t{i}", target="x")])
    data = await store.get(sid)
    assert data is not None
    assert len(data["actions"]) == 20
    assert data["actions"][0]["tool"] == "t5"
    assert data["actions"][-1]["tool"] == "t24"
