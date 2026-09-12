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


@pytest.mark.asyncio
async def test_schema_initialized_once_per_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lattice.session as session_mod

    calls = {"n": 0}
    real = session_mod._ensure_column

    async def counting(conn, table, column, declaration):
        calls["n"] += 1
        return await real(conn, table, column, declaration)

    fingerprint = {"v": (1, 1)}
    monkeypatch.setattr(session_mod, "_ensure_column", counting)
    monkeypatch.setattr(session_mod, "_schema_fingerprint", lambda path: fingerprint["v"])

    store = SessionStore(tmp_path / "state.db")
    sid = await store.create(profile_id="default", user_id="u", channel="cli")
    await store.get(sid)
    await store.get_sticky_primary_model("cli", "u")
    assert calls["n"] == 1

    # Same path, new file (e.g. an eval run home recreated): DDL runs again.
    fingerprint["v"] = (2, 2)
    await store.get(sid)
    assert calls["n"] == 2
