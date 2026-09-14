"""Aggregation over turns.jsonl."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lattice.stats import compute_stats, load_records, stats_for_home
from lattice.turn_record import turn_records_path


def _record(**overrides) -> dict:
    record = {
        "turn_id": "t",
        "started_at": "2026-09-12T10:00:00+00:00",
        "ended_at": "2026-09-12T10:00:01+00:00",
        "duration_ms": 100,
        "outcome": "completed",
        "tool_ms": 0,
        "tools_offered": 10,
        "tools": [],
        "retry_count": 0,
        "ttft_ms": 10,
        "usage": {"requests": 1, "input_tokens": 0, "cache_read_tokens": 0},
        "context": {},
    }
    record.update(overrides)
    return record


def _fixture() -> list[dict]:
    return [
        _record(
            duration_ms=100,
            ttft_ms=10,
            phases={"prefetch": 10, "executor": 100},
            usage={"requests": 1, "input_tokens": 1000, "cache_read_tokens": 900},
            tools=[
                {"name": "shell", "duration_ms": 5, "ok": True, "result_bytes": 1},
                {"name": "read_file", "duration_ms": 3, "ok": True, "result_bytes": 1},
            ],
        ),
        _record(
            duration_ms=300,
            ttft_ms=20,
            outcome="error",
            retry_count=1,
            phases={"prefetch": 20, "executor": 200, "compress": 5},
            context={"compressed": True},
            usage={"requests": 2, "input_tokens": 2000, "cache_read_tokens": 1000},
            tools=[
                {"name": "shell", "duration_ms": 10, "ok": True, "result_bytes": 1},
                {"name": "shell", "duration_ms": 4, "ok": False, "result_bytes": 1},
            ],
        ),
        _record(
            duration_ms=200,
            ttft_ms=None,
            outcome="timeout",
            usage={"requests": 1, "input_tokens": 500, "cache_read_tokens": 0},
            tools=[{"name": "calculator", "duration_ms": 2, "ok": False, "result_bytes": 1}],
        ),
    ]


def test_compute_stats_aggregates() -> None:
    data = compute_stats(_fixture())
    assert data["turns"] == 3
    assert data["outcomes"] == {"completed": 1, "error": 1, "timeout": 1}
    assert data["duration_ms"] == {"p50": 200, "p95": 300}
    assert data["ttft_ms"] == {"p50": 10, "p95": 20}
    assert data["requests"] == 4
    assert data["tool_calls"] == 5
    assert data["cache_hit_ratio"] == 1900 / 3500
    assert data["retries"] == 1
    assert data["turns_with_retries"] == 1
    assert data["compressions"] == 1
    assert data["phases"]["prefetch"] == {"total_ms": 30, "p50": 10, "p95": 20}
    assert data["phases"]["executor"] == {"total_ms": 300, "p50": 100, "p95": 200}
    assert data["phases"]["compress"] == {"total_ms": 5, "p50": 5, "p95": 5}
    assert dict(data["top_failing_tools"]) == {"files/shell": 1, "compute/calculator": 1}
    assert data["top_expensive_tools"][0] == ("files/shell", 19)


def test_compute_stats_empty() -> None:
    data = compute_stats([])
    assert data["turns"] == 0
    assert data["duration_ms"] == {"p50": None, "p95": None}
    assert data["phases"] == {}
    assert data["cache_hit_ratio"] == 0.0


def test_load_records_filters_by_days(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    now = datetime.now(UTC)
    old = _record(ended_at=(now - timedelta(days=5)).isoformat())
    new = _record(ended_at=now.isoformat())
    path.write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n", encoding="utf-8")
    assert len(load_records(path)) == 2
    assert len(load_records(path, days=1)) == 1


def test_stats_for_home_reads_records_path(tmp_path: Path) -> None:
    path = turn_records_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    assert stats_for_home(tmp_path)["turns"] == 1
