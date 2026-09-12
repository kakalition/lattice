"""Cancellation end-to-end: cooperative event + process-group cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from lattice.config import LatticeSettings
from lattice.memory import InMemoryMemory
from lattice.models import Inbound
from lattice.session import SessionStore
from lattice.setup import init_home
from lattice.turn import run_turn


class _FakeResult:
    output = "never"
    usage: Any

    def __init__(self) -> None:
        from pydantic_ai.usage import RunUsage

        self.usage = RunUsage()

    def new_messages(self) -> list[Any]:
        return []


class _SlowAgent:
    async def run(self, *args: Any, **kwargs: Any) -> _FakeResult:
        await asyncio.sleep(5)
        return _FakeResult()


@pytest.mark.asyncio
async def test_cancel_event_unwinds_promptly_and_persists_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    monkeypatch.setattr("lattice.turn.create_agent", lambda *a, **k: _SlowAgent())
    store = SessionStore(tmp_path / "state.db")
    cancel_event = asyncio.Event()

    async def _cancel_soon() -> None:
        await asyncio.sleep(0.1)
        cancel_event.set()

    canceller = asyncio.create_task(_cancel_soon())
    out = await asyncio.wait_for(
        run_turn(
            Inbound(text="do a long thing", profile_id="default", channel="cli"),
            settings=settings,
            cancel_event=cancel_event,
            session_store=store,
            memory=InMemoryMemory("t"),
        ),
        timeout=3,
    )
    await canceller
    assert out.text == "[cancelled]"
    saved = await store.get(out.session_id or "")
    assert saved is not None
    assert saved["messages"][-1]["content"] == "[cancelled]"


@pytest.mark.asyncio
async def test_shell_cancel_kills_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    import lattice.tools.shell as shell_mod

    calls: list[int] = []
    real_killpg = shell_mod.os.killpg

    def spy(pid: int, sig: int) -> None:
        calls.append(pid)
        real_killpg(pid, sig)

    monkeypatch.setattr(shell_mod.os, "killpg", spy)
    task = asyncio.create_task(shell_mod.run_shell("sleep 30", timeout=60))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls, "process group should be killed on cancellation"
