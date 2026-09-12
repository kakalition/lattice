"""Background memory writer: off the reply path, ordered, flushable."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from lattice.memory.worker import enqueue_sync, flush_memory, pending_jobs
from lattice.models import Inbound
from lattice.setup import init_home
from lattice.turn import run_turn


class SlowMemory:
    def __init__(self, delay: float = 2.0) -> None:
        self.delay = delay
        self.synced: list[list[dict[str, Any]]] = []

    async def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return []

    async def add(self, text: str, *, metadata: dict | None = None, infer: bool = True) -> str:
        return "x"

    async def update(self, memory_id: str, text: str) -> None:
        return None

    async def forget(self, memory_id: str) -> None:
        return None

    async def sync_turn(self, messages: list[dict[str, Any]]) -> None:
        await asyncio.sleep(self.delay)
        self.synced.append(list(messages))


@pytest.mark.asyncio
async def test_run_turn_returns_before_slow_memory_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_home(tmp_path)
    memory = SlowMemory(delay=2.0)
    monkeypatch.setattr("lattice.turn.build_memory_for_profile", lambda *a, **k: memory)

    from lattice.config import LatticeSettings
    from lattice.session import SessionStore

    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    store = SessionStore(tmp_path / "state.db")

    started = time.monotonic()
    out = await run_turn(
        Inbound(text="hello", profile_id="default", channel="cli", user_id="u"),
        settings=settings,
        session_store=store,
        model=TestModel(call_tools=[], custom_output_text="quick"),
    )
    elapsed = time.monotonic() - started

    assert out.text == "quick"
    # The 2s write must not gate the reply.
    assert elapsed < 1.0
    await flush_memory(timeout=5.0)
    assert memory.synced, "queued write should eventually run"


class RecordingMemory:
    def __init__(self) -> None:
        self.order: list[str] = []
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return []

    async def add(self, text: str, *, metadata: dict | None = None, infer: bool = True) -> str:
        return "x"

    async def update(self, memory_id: str, text: str) -> None:
        return None

    async def forget(self, memory_id: str) -> None:
        return None

    async def sync_turn(self, messages: list[dict[str, Any]]) -> None:
        self.started.set()
        await self.gate.wait()
        self.order.append(str(messages[0].get("content")))


@pytest.mark.asyncio
async def test_queue_is_serialized_and_ordered() -> None:
    memory = RecordingMemory()
    for label in ("a", "b", "c"):
        enqueue_sync(memory, [{"role": "user", "content": label}])  # type: ignore[arg-type]
    await memory.started.wait()
    memory.gate.set()
    await flush_memory(timeout=5.0)
    assert memory.order == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_flush_drains_queue() -> None:
    memory = SlowMemory(delay=0.05)
    enqueue_sync(memory, [{"role": "user", "content": "x"}])  # type: ignore[arg-type]
    assert pending_jobs() >= 1
    await flush_memory(timeout=5.0)
    assert pending_jobs() == 0
