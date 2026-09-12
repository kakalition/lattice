"""Retry safety and typed provider errors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.usage import RunUsage

from lattice.config import LatticeSettings
from lattice.memory import InMemoryMemory
from lattice.models import Inbound
from lattice.providers.errors import FailoverReason, classify_provider_error
from lattice.session import SessionStore
from lattice.setup import init_home
from lattice.turn import run_turn


class _Result:
    def __init__(self, output: str = "") -> None:
        self.output = output
        self.usage = RunUsage()

    def new_messages(self) -> list[Any]:
        return []


class _FailAfterToolAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, *args: Any, **kwargs: Any) -> _Result:
        self.calls += 1
        deps = kwargs["deps"]
        await deps.events.on_tool_start("read_file", {"path": "x.py"})
        raise RuntimeError("429 rate limit")


class _EmptyAfterToolAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, *args: Any, **kwargs: Any) -> _Result:
        self.calls += 1
        deps = kwargs["deps"]
        await deps.events.on_tool_start("read_file", {"path": "x.py"})
        return _Result(output="")


@pytest.mark.asyncio
async def test_post_tool_retryable_error_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    agent = _FailAfterToolAgent()
    monkeypatch.setattr("lattice.turn.create_agent", lambda *a, **k: agent)
    out = await run_turn(
        Inbound(text="read it", profile_id="default", channel="cli"),
        settings=settings,
        session_store=SessionStore(tmp_path / "state.db"),
        memory=InMemoryMemory("t"),
    )
    assert agent.calls == 1, "must not replay tools on retry"
    assert "continue" in out.text.lower()


@pytest.mark.asyncio
async def test_empty_completion_after_tools_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    agent = _EmptyAfterToolAgent()
    monkeypatch.setattr("lattice.turn.create_agent", lambda *a, **k: agent)
    out = await run_turn(
        Inbound(text="read it", profile_id="default", channel="cli"),
        settings=settings,
        session_store=SessionStore(tmp_path / "state.db"),
        memory=InMemoryMemory("t"),
    )
    assert agent.calls == 1
    assert out.text == "(no reply)"


class _HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.status_code = status


def test_typed_status_codes_map_to_reasons() -> None:
    assert classify_provider_error(_HttpError(429)) == FailoverReason.RATE_LIMIT
    assert classify_provider_error(_HttpError(503)) == FailoverReason.TRANSIENT
    assert classify_provider_error(_HttpError(401)) == FailoverReason.AUTH


def test_context_overflow_precedence_fixed() -> None:
    assert (
        classify_provider_error(RuntimeError("maximum context length exceeded"))
        == FailoverReason.CONTEXT_OVERFLOW
    )
    assert (
        classify_provider_error(RuntimeError("empty completion")) == FailoverReason.EMPTY_COMPLETION
    )


def test_token_limit_branch_requires_both_words() -> None:
    assert classify_provider_error(RuntimeError("token budget issue")) == FailoverReason.FATAL
    assert (
        classify_provider_error(RuntimeError("token limit reached"))
        == FailoverReason.CONTEXT_OVERFLOW
    )
