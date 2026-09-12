"""Optional bounded memory recall (``memory.search_timeout_seconds``)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import lattice.turn as turn_mod
from lattice.config import LatticeSettings
from lattice.memory import InMemoryMemory
from lattice.models import Inbound
from lattice.session import SessionStore
from lattice.setup import init_home
from lattice.turn import run_turn


class SlowMemory(InMemoryMemory):
    async def search(self, query: str, *, limit: int = 5) -> list[dict]:
        await asyncio.sleep(5)
        return [{"text": "should never arrive"}]


class RecallMemory(InMemoryMemory):
    async def search(self, query: str, *, limit: int = 5) -> list[dict]:
        return [{"text": "remembered fact"}]


def _capture_notices(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}
    real = turn_mod.build_prompt_bundle

    def spy(profile, entries, notices, *, runtime_context=""):
        captured["notices"] = list(notices)
        return real(profile, entries, notices, runtime_context=runtime_context)

    monkeypatch.setattr(turn_mod, "build_prompt_bundle", spy)
    return captured


async def _run(tmp_path: Path, settings: LatticeSettings, memory) -> str:
    from pydantic_ai.models.test import TestModel

    out = await run_turn(
        Inbound(text="hi", profile_id="default", channel="cli", user_id="u"),
        settings=settings,
        session_store=SessionStore(tmp_path / "state.db"),
        model=TestModel(call_tools=[], custom_output_text="ok"),
        memory=memory,
    )
    return out.text


@pytest.mark.asyncio
async def test_memory_timeout_drops_recall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_notices(monkeypatch)
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    settings.memory.search_timeout_seconds = 0.01

    text = await _run(tmp_path, settings, SlowMemory("t"))

    assert text == "ok"
    assert not any("Relevant memories" in notice for notice in captured["notices"])


@pytest.mark.asyncio
async def test_memory_recall_notice_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_notices(monkeypatch)
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    assert settings.memory.search_timeout_seconds == 0.0

    await _run(tmp_path, settings, RecallMemory("t"))

    assert any(
        "Relevant memories" in notice and "remembered fact" in notice
        for notice in captured["notices"]
    )
