"""Auto-attach of media produced during a turn."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import lattice.turn as turn_mod
from lattice.turn import discover_turn_media, snapshot_media


def _touch(path: Path, mtime: float, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (mtime, mtime))
    return path


def test_discovers_script_produced_media_after_turn_start(tmp_path: Path) -> None:
    turn_start = time.time()
    old = _touch(tmp_path / "old.png", turn_start - 600)
    before = snapshot_media(tmp_path)
    fresh = _touch(tmp_path / "chart.png", turn_start + 0.5)

    found = discover_turn_media(tmp_path, turn_start=turn_start, before=before, existing=set())

    assert found == [fresh.resolve()]
    assert old.resolve() not in found


def test_inbound_uploads_are_not_re_attached(tmp_path: Path) -> None:
    turn_start = time.time()
    before = snapshot_media(tmp_path)
    _touch(tmp_path / "inbound" / "photo-1.jpg", turn_start + 0.5)

    found = discover_turn_media(tmp_path, turn_start=turn_start, before=before, existing=set())

    assert found == []


def test_dedupes_against_existing_outbound_media(tmp_path: Path) -> None:
    turn_start = time.time()
    before = snapshot_media(tmp_path)
    chart = _touch(tmp_path / "chart.png", turn_start + 0.5)

    found = discover_turn_media(
        tmp_path, turn_start=turn_start, before=before, existing={chart.resolve()}
    )

    assert found == []


@pytest.mark.asyncio
async def test_pure_text_turn_skips_media_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.models.test import TestModel

    from lattice.config import LatticeSettings
    from lattice.memory import InMemoryMemory
    from lattice.models import Inbound
    from lattice.session import SessionStore
    from lattice.setup import init_home
    from lattice.turn import run_turn

    calls = {"snapshot": 0, "discover": 0}
    real_discover = turn_mod.discover_turn_media
    real_snapshot = turn_mod.snapshot_media

    def counting_discover(*args, **kwargs):
        calls["discover"] += 1
        return real_discover(*args, **kwargs)

    def counting_snapshot(*args, **kwargs):
        calls["snapshot"] += 1
        return real_snapshot(*args, **kwargs)

    monkeypatch.setattr(turn_mod, "discover_turn_media", counting_discover)
    monkeypatch.setattr(turn_mod, "snapshot_media", counting_snapshot)
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    await run_turn(
        Inbound(text="just chat", profile_id="default", channel="cli", user_id="u"),
        settings=settings,
        session_store=SessionStore(tmp_path / "state.db"),
        model=TestModel(call_tools=[], custom_output_text="hi"),
        memory=InMemoryMemory("t"),
    )
    assert calls == {"snapshot": 0, "discover": 0}


@pytest.mark.asyncio
async def test_media_tool_turn_snapshots_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.models.test import TestModel

    from lattice.config import LatticeSettings
    from lattice.memory import InMemoryMemory
    from lattice.models import Inbound
    from lattice.session import SessionStore
    from lattice.setup import init_home
    from lattice.turn import run_turn

    calls = {"snapshot": 0}
    real_snapshot = turn_mod.snapshot_media

    def counting_snapshot(*args, **kwargs):
        calls["snapshot"] += 1
        return real_snapshot(*args, **kwargs)

    monkeypatch.setattr(turn_mod, "snapshot_media", counting_snapshot)
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    await run_turn(
        Inbound(text="write a note", profile_id="default", channel="cli", user_id="u"),
        settings=settings,
        session_store=SessionStore(tmp_path / "state.db"),
        model=TestModel(call_tools=["write_file"], custom_output_text="done"),
        memory=InMemoryMemory("t"),
    )
    assert calls["snapshot"] == 1


def test_caps_count_and_ignores_non_media(tmp_path: Path) -> None:
    turn_start = time.time()
    before = snapshot_media(tmp_path)
    for i in range(10):
        _touch(tmp_path / f"chart-{i}.png", turn_start + 0.5 + i)
    _touch(tmp_path / "notes.txt", turn_start + 0.5)

    found = discover_turn_media(tmp_path, turn_start=turn_start, before=before, existing=set())

    assert len(found) == 6
    # Oldest first, so the earliest produced chart survives the cap.
    assert found[0].name == "chart-0.png"
