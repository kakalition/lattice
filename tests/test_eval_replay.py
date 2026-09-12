"""Cassette record/replay round-trip (offline; run with -m eval)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel

from lattice.eval.cassette import (
    CassetteRecorder,
    build_replay_model,
    cassette_sink,
    request_digest,
)
from lattice.providers.logging_model import LoggingModel

pytestmark = pytest.mark.eval


def _messages(text: str) -> list[ModelRequest]:
    return [ModelRequest(parts=[UserPromptPart(content=text)])]


@pytest.mark.asyncio
async def test_record_then_replay_matches(tmp_path: Path) -> None:
    messages = _messages("hello")
    params = ModelRequestParameters()
    recorder = CassetteRecorder(tmp_path, "turn1")
    token = cassette_sink.set(recorder)
    try:
        model = LoggingModel(TestModel(custom_output_text="hello back"))
        recorded = await model.request(messages, None, params)
    finally:
        cassette_sink.reset(token)

    assert str(recorded.parts[0].content) == "hello back"
    path = tmp_path / "turn1.jsonl"
    assert path.is_file()

    replay = build_replay_model(path)
    replayed = await replay.request(messages, None, params)
    assert str(replayed.parts[0].content) == "hello back"
    assert replay.prompt_drift is False
    assert replay.matched_digest is True


@pytest.mark.asyncio
async def test_mutated_prompt_records_drift_but_still_replays(tmp_path: Path) -> None:
    recorder = CassetteRecorder(tmp_path, "turn2")
    token = cassette_sink.set(recorder)
    try:
        model = LoggingModel(TestModel(custom_output_text="first answer"))
        await model.request(_messages("original"), None, ModelRequestParameters())
    finally:
        cassette_sink.reset(token)

    replay = build_replay_model(tmp_path / "turn2.jsonl")
    mutated = await replay.request(_messages("changed prompt"), None, ModelRequestParameters())
    assert str(mutated.parts[0].content) == "first answer"
    assert replay.prompt_drift is True


def test_digest_ignores_notices_and_timestamps() -> None:
    a = _messages("[notice] Runtime: now=2026-01-01\n\nreal question")
    b = _messages("[notice] Runtime: now=2030-09-09\n\nreal question")
    assert request_digest(a) == request_digest(b)


@pytest.mark.asyncio
async def test_replay_stream_serves_recorded_response(tmp_path: Path) -> None:
    recorder = CassetteRecorder(tmp_path, "turn3")
    token = cassette_sink.set(recorder)
    try:
        model = LoggingModel(TestModel(custom_output_text="streamed back"))
        await model.request(_messages("hello"), None, ModelRequestParameters())
    finally:
        cassette_sink.reset(token)

    replay = build_replay_model(tmp_path / "turn3.jsonl")
    async with replay.request_stream(_messages("hello"), None, ModelRequestParameters()) as stream:
        async for _ in stream:
            pass
        response = stream.get()
    assert str(response.parts[0].content) == "streamed back"
