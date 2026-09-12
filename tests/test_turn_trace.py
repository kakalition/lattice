"""Turn trace logging tests."""

from __future__ import annotations

import logging

import pytest

from lattice.turn_trace import LoggingTurnEvents


@pytest.mark.asyncio
async def test_logging_turn_events_records_tools_and_skills(caplog) -> None:
    trace = LoggingTurnEvents("abcd1234")
    with caplog.at_level(logging.INFO, logger="lattice.turn"):
        trace.log_begin(
            channel="telegram",
            user_id="1",
            profile_id="default",
            session_id="s1",
            inbound_text="remind me",
            tools=["schedule_add", "todo"],
            skills=[("safe-shell", "shell skill")],
        )
        await trace.on_tool_start("schedule_add", {"reminder": "journal", "run_at": "x"})
        await trace.on_tool_end("schedule_add", "scheduled job-1")
        await trace.on_tool_start("skill_view", {"name": "weekly-review"})
        await trace.on_tool_end("skill_view", "body…")
        trace.log_end(outbound_text="done")
    text = "\n".join(r.message for r in caplog.records)
    assert "turn=abcd1234 BEGIN" in text
    assert "tools_offered" in text and "schedule_add" in text
    assert "skills_offered" in text and "safe-shell" in text
    assert "skill_meta" not in text
    assert "tool_start: schedule_add" in text
    assert "skill_load: weekly-review" in text
    assert "outbound: done" in text
    assert "END" in text


@pytest.mark.asyncio
async def test_logging_turn_events_reports_phase_timings(caplog) -> None:
    trace = LoggingTurnEvents("timing123")
    with caplog.at_level(logging.INFO, logger="lattice.turn"):
        with trace.timed("executor"):
            pass
        trace.log_end(
            outbound_text="done",
            timings=trace.phases,
        )
    text = "\n".join(r.message for r in caplog.records)
    assert "executor_ms=" in text


@pytest.mark.asyncio
async def test_logging_turn_events_tool_durations_and_ttft(caplog) -> None:
    trace = LoggingTurnEvents("tools123")
    with caplog.at_level(logging.INFO, logger="lattice.turn"):
        await trace.on_tool_start("read_file", {"path": "a.py"})
        await trace.on_tool_end("read_file", "contents")
        await trace.on_stream_delta("hi")
        await trace.on_stream_delta(" there")
        trace.log_end(outbound_text="done", outcome="completed", retries=2)
    assert len(trace.tools) == 1
    assert trace.tools[0].name == "read_file"
    assert trace.tools[0].ok is True
    assert trace.ttft_ms is not None
    assert trace.stream_chunks == 2
    text = "\n".join(r.message for r in caplog.records)
    assert "tool_ms=" in text
    assert "outcome=completed" in text
    assert "retries=2" in text
    assert "ttft_ms=" in text


@pytest.mark.asyncio
async def test_logging_turn_events_marks_failed_tools(caplog) -> None:
    trace = LoggingTurnEvents("fail123")
    with caplog.at_level(logging.INFO, logger="lattice.turn"):
        await trace.on_tool_start("shell", {"command": "rm -rf /"})
        await trace.on_tool_end("shell", "denied: user said no")
    assert trace.tools[0].ok is False


@pytest.mark.asyncio
async def test_logging_turn_events_adds_cost_to_usage_line(caplog) -> None:
    trace = LoggingTurnEvents("cost123")
    with caplog.at_level(logging.INFO, logger="lattice.turn"):
        trace.log_end(
            outbound_text="done",
            usage={"model": "m", "cost": 0.00123, "requests": 2},
        )
    text = "\n".join(r.message for r in caplog.records)
    assert "cost=0.001230" in text
