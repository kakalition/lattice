"""MCP bridge + scheduler smoke tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lattice.config import McpDeferMode, ToolsConfig
from lattice.mcp import (
    McpHostManager,
    McpToolInfo,
    should_defer_mcp,
    tool_describe,
    tool_search,
)
from lattice.scheduler import (
    Job,
    SchedulerRunner,
    cron_matches,
    is_job_due,
    job_to_inbound,
    load_jobs,
    save_jobs,
)


def test_mcp_defer_and_bridge() -> None:
    mgr = McpHostManager()
    mgr.register_discovered(
        [McpToolInfo(server="s", name=f"t{i}", description=f"tool {i}") for i in range(10)]
    )
    cfg = ToolsConfig(mcp_defer=McpDeferMode.AUTO, mcp_defer_threshold=8)
    assert should_defer_mcp(mgr, cfg)
    assert "t3" in tool_search(mgr, "t3")
    assert "schema" in tool_describe(mgr, "t3")


def test_scheduler_jobs_roundtrip(tmp_path: Path) -> None:
    jobs = [Job(id="j1", prompt="ping", profile="work", deliver="none", schedule="0 * * * *")]
    save_jobs(jobs, tmp_path)
    loaded = load_jobs(tmp_path)
    assert loaded[0].profile == "work"
    inbound = job_to_inbound(loaded[0])
    assert inbound.channel == "scheduler"
    assert inbound.profile_id == "work"


def test_cron_matches() -> None:
    when = datetime(2026, 9, 10, 14, 30, tzinfo=UTC)  # Thu
    assert cron_matches("* * * * *", when)
    assert cron_matches("30 14 * * *", when)
    assert not cron_matches("0 14 * * *", when)
    assert cron_matches("*/15 * * * *", when)
    assert cron_matches("@once", when)


def test_is_job_due_once_and_interval(tmp_path: Path) -> None:
    now = datetime(2026, 9, 10, 14, 30, tzinfo=UTC)
    once = Job(id="o", prompt="x", schedule="@once", enabled=True)
    assert is_job_due(once, now)
    once.last_run = now.isoformat()
    assert not is_job_due(once, now)

    star = Job(id="s", prompt="y", schedule="* * * * *", enabled=True)
    assert is_job_due(star, now)
    star.last_run = now.isoformat()
    assert not is_job_due(star, now, min_interval=timedelta(seconds=55))
    assert is_job_due(star, now + timedelta(seconds=60), min_interval=timedelta(seconds=55))


@pytest.mark.asyncio
async def test_scheduler_run_once(tmp_path: Path) -> None:
    save_jobs(
        [Job(id="pulse", prompt="Say hi", profile="default", schedule="@once", deliver="none")],
        tmp_path,
    )
    runner = SchedulerRunner(home=tmp_path)
    calls: list[str] = []

    async def fake_turn(inbound):
        calls.append(inbound.text)
        from lattice.models import Outbound

        return Outbound(text="done", profile_id=inbound.profile_id)

    out = await runner.run_once(fake_turn)
    assert out == ["done"]
    assert calls == ["Say hi"]
    loaded = load_jobs(tmp_path)
    assert loaded[0].last_run is not None
    assert loaded[0].enabled is False  # @once disabled
    # second tick: nothing due
    assert await runner.run_once(fake_turn) == []


def test_gateway_pidfile(tmp_path: Path) -> None:
    from lattice.gateway import PidfileLock

    lock = PidfileLock(tmp_path / "gateway.pid")
    lock.acquire()
    assert (tmp_path / "gateway.pid").exists()
    lock.release()
    assert not (tmp_path / "gateway.pid").exists()
