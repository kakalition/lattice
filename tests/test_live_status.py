"""Live channel status event tests."""

from __future__ import annotations

import asyncio

import pytest

from lattice.channel.live_status import (
    LiveTurnEvents,
    PhraseDeck,
    bind_live_events,
    current_live_events,
    format_status_message,
    idle_phrase,
    thinking_phrase,
    warm_confirmation,
)


def _has_emoji(text: str) -> bool:
    return any(ord(ch) > 0x2190 for ch in text)


def test_thinking_phrases_are_creative_with_emoji() -> None:
    seen = {thinking_phrase() for _ in range(300)}
    assert len(seen) == 300, "deck should not repeat within a cycle"
    assert all(_has_emoji(p) for p in seen)
    joined = " ".join(seen)
    for tool in ("shell", "read_file", "sqlite", "web_search", "execute_script"):
        assert tool not in joined


def test_phrase_deck_is_combinatorial() -> None:
    deck = PhraseDeck(["a", "b"], ["x", "y", "z"], ["😀", "😎"])
    assert deck.size == 12
    assert len({deck.next() for _ in range(12)}) == 12


def test_idle_phrase_custom_pool() -> None:
    assert idle_phrase(0, ("alpha…", "bravo…")) == "alpha…"
    assert idle_phrase(1, ("alpha…", "bravo…")) == "bravo…"


def test_format_status_message_hides_tools() -> None:
    out = format_status_message("hitl_ask shell: rm -rf")
    assert _has_emoji(out)
    assert "shell" not in out and "rm" not in out
    assert _has_emoji(format_status_message("routing"))


def test_warm_confirmation() -> None:
    for ack in ("ok", "Okay.", "sure!", "done", "Got it", "👍"):
        out = warm_confirmation(ack)
        assert out and _has_emoji(out)
    assert warm_confirmation("no") is None
    assert warm_confirmation("Here is a full answer with detail.") is None
    assert len({warm_confirmation("ok") for _ in range(50)}) > 1


@pytest.mark.asyncio
async def test_live_turn_events_never_shows_tools() -> None:
    seen: list[str] = []

    class Sink:
        async def set_status(self, text: str) -> None:
            seen.append(text)

    live = LiveTurnEvents(Sink(), min_interval_s=0.0, phrase_interval_s=10.0)
    async with bind_live_events(live):
        assert current_live_events() is live
        await live.on_status("loading session")
        await live.on_tool_start("shell", {"command": "rm -rf /tmp/secret"})
        await live.on_tool_end("shell", "done")
    assert current_live_events() is None
    assert seen
    joined = " ".join(seen)
    assert "shell" not in joined
    assert "secret" not in joined
    assert "loading" not in joined


@pytest.mark.asyncio
async def test_live_turn_events_coalesces_pending() -> None:
    seen: list[str] = []

    class Sink:
        async def set_status(self, text: str) -> None:
            seen.append(text)

    phrases = ("alpha…", "bravo…", "charlie…", "delta…", "echo…")
    live = LiveTurnEvents(Sink(), min_interval_s=0.2, phrase_interval_s=10.0, phrases=phrases)
    await live.on_status("x")
    await live.on_tool_start("shell", {})
    await asyncio.sleep(0.25)
    await live.close()
    assert seen[0] == "alpha…"
    assert "bravo…" in seen


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
