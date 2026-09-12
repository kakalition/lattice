"""Structured turn records: one parseable JSON line per turn."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from lattice.config import LatticeSettings
from lattice.models import Inbound
from lattice.session import SessionStore
from lattice.setup import init_home
from lattice.turn import run_turn
from lattice.turn_record import turn_records_path
from lattice.turn_trace import LoggingTurnEvents


@pytest.mark.asyncio
async def test_turn_record_written(tmp_path: Path) -> None:
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    store = SessionStore(tmp_path / "state.db")
    out = await run_turn(
        Inbound(text="hello", profile_id="default", channel="cli", user_id="u"),
        settings=settings,
        session_store=store,
        model=TestModel(call_tools=[], custom_output_text="ok"),
    )
    assert out.text == "ok"

    path = turn_records_path(tmp_path)
    assert path.is_file()
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    for key in (
        "turn_id",
        "started_at",
        "ended_at",
        "duration_ms",
        "channel",
        "profile_id",
        "session_id",
        "outcome",
        "phases",
        "tools",
        "retry_count",
        "usage",
        "context",
    ):
        assert key in record, key
    assert record["outcome"] == "completed"
    assert record["channel"] == "cli"
    assert record["context"]["before_messages"] == 0
    assert record["context"]["after_messages"] == 2


def test_turn_record_write_failure_does_not_raise(tmp_path: Path) -> None:
    # Make ``logs`` a file so creating logs/turns.jsonl fails.
    (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
    trace = LoggingTurnEvents("badwrite", home=tmp_path)
    trace.log_begin(
        channel="cli",
        user_id="u",
        profile_id="default",
        session_id="s",
        inbound_text="hi",
        tools=[],
        skills=[],
    )
    trace.log_end(outbound_text="done")  # must not raise


def test_turn_record_disabled_writes_nothing(tmp_path: Path) -> None:
    trace = LoggingTurnEvents("off", home=tmp_path, record_enabled=False)
    trace.log_begin(
        channel="cli",
        user_id="u",
        profile_id="default",
        session_id="s",
        inbound_text="hi",
        tools=[],
        skills=[],
    )
    trace.log_end(outbound_text="done")
    assert not turn_records_path(tmp_path).exists()
