"""In-turn compression recovery and transcript fidelity."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai.messages import (
    CachePoint,
    ModelRequest,
    SystemPromptPart,
    TextContent,
    UserPromptPart,
)

from lattice.context import compress
from lattice.session_history import session_dicts_to_history


def _msgs(n: int, *, tool: bool = False) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if tool:
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "1",
                        "function": {"name": "read_file", "arguments": '{"path": "x.py"}'},
                    }
                ],
            }
        )
        messages.append({"role": "tool", "content": "body", "tool_call_id": "1"})
    messages.extend({"role": "user", "content": f"m{i}"} for i in range(n))
    return messages


@pytest.mark.asyncio
async def test_forced_compress_shrinks_without_aux() -> None:
    messages = _msgs(30)
    result = await compress(messages, aux=None, protect_last_n=5, force=True)
    assert result.compressed is True
    assert result.used_trim_fallback is True
    assert len(result.messages) < len(messages)


@pytest.mark.asyncio
async def test_second_compression_yields_single_summary() -> None:
    first = await compress(_msgs(30), aux=None, protect_last_n=5, force=True)
    second = await compress(first.messages, aux=None, protect_last_n=3, force=True)
    summaries = [
        m
        for m in second.messages
        if str(m.get("content") or "").startswith("[compressed context summary]")
    ]
    assert len(summaries) == 1


@pytest.mark.asyncio
async def test_trim_fallback_keeps_newest() -> None:
    messages = _msgs(20)
    result = await compress(messages, aux=None, protect_last_n=2, force=True)
    # middle is m0..m17; the newest quarter (m17) must survive, m0 must not.
    assert "m17" in result.summary
    assert "m0" not in result.summary


class _StubSummarizer:
    def __init__(self) -> None:
        self.transcripts: list[str] = []

    async def summarize(self, transcript: str, *, max_words: int = 400) -> str:
        self.transcripts.append(transcript)
        return "summary"


@pytest.mark.asyncio
async def test_transcript_includes_tool_name_and_args() -> None:
    stub = _StubSummarizer()
    result = await compress(_msgs(10, tool=True), aux=stub, protect_last_n=2, force=True)
    assert result.compressed is True
    transcript = stub.transcripts[0]
    assert "read_file" in transcript
    assert "x.py" in transcript


@pytest.mark.asyncio
async def test_compressed_history_carries_summary_as_user_turn() -> None:
    result = await compress(_msgs(30), aux=None, protect_last_n=5, force=True)
    history = session_dicts_to_history(result.messages)
    assert any(
        "compressed context summary" in str(part.content)
        for message in history
        for part in message.parts
    )


def test_system_prompt_prepended_on_non_empty_history() -> None:
    history = session_dicts_to_history(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        system_prompt="SYS",
    )
    first = history[0]
    assert isinstance(first, ModelRequest)
    assert isinstance(first.parts[0], SystemPromptPart)
    assert first.parts[0].content == "SYS"


def test_no_system_prompt_added_on_empty_history() -> None:
    assert session_dicts_to_history([], system_prompt="SYS") == []


def test_stored_system_message_is_not_duplicated() -> None:
    history = session_dicts_to_history(
        [{"role": "system", "content": "stored"}, {"role": "user", "content": "hi"}],
        system_prompt="SYS",
    )
    system_texts = [
        part.content
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, SystemPromptPart)
    ]
    assert system_texts == ["stored"]


def test_cache_point_lands_on_newest_user_prompt_with_system_prefix() -> None:
    history = session_dicts_to_history(
        [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "new"},
        ],
        system_prompt="SYS",
        cache_boundary=True,
    )
    newest = history[-1]
    assert isinstance(newest, ModelRequest)
    part = newest.parts[0]
    assert isinstance(part, UserPromptPart)
    assert isinstance(part.content, list)
    assert isinstance(part.content[0], TextContent)
    assert part.content[0].content == "new"
    assert isinstance(part.content[-1], CachePoint)
