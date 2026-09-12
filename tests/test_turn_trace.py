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
        with trace.timed("routing"), trace.timed("classifier"):
            pass
        with trace.timed("executor"):
            pass
        trace.log_end(
            outbound_text="done",
            route="low",
            route_source="classifier",
            timings=trace.phases,
            usage={"wasted_worker_usage": {"model": "worker", "input_tokens": 3}},
        )
    text = "\n".join(r.message for r in caplog.records)
    assert "routing_ms=" in text
    assert "classifier_ms=" in text
    assert "executor_ms=" in text
    assert "source=classifier" in text
    assert "wasted_worker_usage: model=worker input=3" in text
