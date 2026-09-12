"""Atomic session appends: concurrent writers must not lose updates."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lattice.session import SessionStore


@pytest.mark.asyncio
async def test_concurrent_appends_all_survive(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state.db")
    sid = await store.create(profile_id="default", user_id="u", channel="cli")
    await asyncio.gather(
        *(store.append_message(sid, {"role": "user", "content": f"m{i}"}) for i in range(12))
    )
    data = await store.get(sid)
    assert data is not None
    contents = [m["content"] for m in data["messages"] if m["role"] == "user"]
    assert len(contents) == 12
    assert set(contents) == {f"m{i}" for i in range(12)}
