"""Scheduler deliver path tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice.models import Outbound
from lattice.scheduler import Job, SchedulerRunner, load_jobs, save_jobs


@pytest.mark.asyncio
async def test_scheduler_deliver_callback(tmp_path: Path) -> None:
    save_jobs(
        [
            Job(
                id="pulse",
                prompt="hi",
                schedule="@once",
                deliver="telegram",
                profile="default",
            )
        ],
        tmp_path,
    )
    runner = SchedulerRunner(home=tmp_path)
    delivered: list[tuple[str, str]] = []

    async def turn_fn(inbound):
        return Outbound(text=f"echo:{inbound.text}", profile_id=inbound.profile_id)

    async def send_fn(channel: str, outbound: Outbound) -> None:
        delivered.append((channel, outbound.text))

    outs = await runner.run_once(turn_fn, send_fn=send_fn)
    assert outs == ["echo:hi"]
    assert delivered == [("telegram", "echo:hi")]
    job = load_jobs(tmp_path)[0]
    assert job.enabled is False
    assert job.last_run is not None


@pytest.mark.asyncio
async def test_scheduler_failure_still_delivers(tmp_path: Path) -> None:
    save_jobs(
        [Job(id="bad", prompt="x", schedule="@once", deliver="cli")],
        tmp_path,
    )
    runner = SchedulerRunner(home=tmp_path)
    delivered: list[str] = []

    async def turn_fn(inbound):
        raise RuntimeError("boom")

    async def send_fn(channel: str, outbound: Outbound) -> None:
        delivered.append(outbound.text)

    outs = await runner.run_once(turn_fn, send_fn=send_fn)
    assert outs and "failed" in outs[0]
    assert delivered and "boom" in delivered[0]
