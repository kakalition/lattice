"""Live channel status event tests."""

from __future__ import annotations

import asyncio

import pytest

from lattice.channel.live_status import (
    IDLE_PHRASES,
    LiveTurnEvents,
    bind_live_events,
    current_live_events,
    format_status_message,
    format_tool_activity,
    idle_phrase,
)


def test_format_tool_activity() -> None:
    assert format_tool_activity("shell", {"command": "ls -la"}).startswith("shell: ls")
    assert "finances" in format_tool_activity("sqlite_query", {"database": "finances"})
    assert format_tool_activity("skill_view", {"name": "weekly-review"}) == "skill: weekly-review…"


def test_format_status_message() -> None:
    assert format_status_message("thinking") == idle_phrase(0)
    assert format_status_message("hitl_ask shell: rm") == "waiting for approval…"
    assert format_status_message("already…") == "already…"
    assert idle_phrase(0) in IDLE_PHRASES
    assert idle_phrase(1) != idle_phrase(0)


@pytest.mark.asyncio
async def test_live_turn_events_throttles_and_binds() -> None:
    seen: list[str] = []

    class Sink:
        async def set_status(self, text: str) -> None:
            seen.append(text)

    live = LiveTurnEvents(Sink(), min_interval_s=0.0, phrase_interval_s=10.0)
    async with bind_live_events(live):
        assert current_live_events() is live
        await live.on_status("loading session")
        await live.on_tool_start("shell", {"command": "echo hi"})
        await live.on_tool_end("shell", "hi")
    assert current_live_events() is None
    assert "loading session…" in seen
    assert any(s.startswith("shell:") for s in seen)
    assert any(s in IDLE_PHRASES for s in seen)


@pytest.mark.asyncio
async def test_live_turn_events_coalesces_pending() -> None:
    seen: list[str] = []

    class Sink:
        async def set_status(self, text: str) -> None:
            seen.append(text)

    live = LiveTurnEvents(Sink(), min_interval_s=0.2, phrase_interval_s=10.0)
    await live.on_status("a")
    await live.on_tool_start("shell", {"command": "one"})
    await live.on_tool_start("shell", {"command": "two"})
    await asyncio.sleep(0.25)
    await live.close()
    assert seen[0] == "a…"
    assert any("two" in s for s in seen)


@pytest.mark.asyncio
async def test_idle_phrases_rotate() -> None:
    seen: list[str] = []

    class Sink:
        async def set_status(self, text: str) -> None:
            seen.append(text)

    phrases = ("alpha…", "bravo…", "charlie…")
    live = LiveTurnEvents(
        Sink(),
        min_interval_s=0.0,
        phrase_interval_s=0.05,
        phrases=phrases,
    )
    async with bind_live_events(live):
        await live.on_status("thinking")
        await asyncio.sleep(0.18)
    assert seen[0] == "alpha…"
    assert "bravo…" in seen
    assert len({s for s in seen if s in phrases}) >= 2
