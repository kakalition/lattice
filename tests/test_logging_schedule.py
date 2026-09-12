"""Logging + schedule reminder tools."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lattice.logging_config import setup_logging
from lattice.models import Outbound
from lattice.scheduler import Job, SchedulerRunner, is_job_due, load_jobs, save_jobs
from lattice.scheduler.tools import schedule_add, schedule_cancel, schedule_list


def test_setup_logging_rotating_file(tmp_path: Path) -> None:
    path = setup_logging(tmp_path, force=True, also_stderr=False)
    assert path == tmp_path / "logs" / "lattice.log"
    logging.getLogger("lattice.test").info("hello-log")
    for h in logging.getLogger().handlers:
        h.flush()
    text = path.read_text(encoding="utf-8")
    assert "hello-log" in text


def test_schedule_add_run_at_and_list(tmp_path: Path) -> None:
    msg = schedule_add(
        reminder="Time to journal",
        home=tmp_path,
        run_at="2026-09-10T22:10:00+07:00",
        deliver="telegram",
        job_id="journal-1",
    )
    assert "scheduled journal-1" in msg
    listed = schedule_list(home=tmp_path)
    assert "journal-1" in listed and "Time to journal" in listed
    assert "cancelled" in schedule_cancel("journal-1", home=tmp_path)
    assert "(no scheduled jobs)" in schedule_list(home=tmp_path)


@pytest.mark.asyncio
async def test_run_at_delivers_message_without_llm(tmp_path: Path) -> None:
    save_jobs(
        [
            Job(
                id="j",
                prompt="ignored-llm",
                message="Journal now",
                schedule="@once",
                run_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                deliver="telegram",
                timezone="Asia/Bangkok",
            )
        ],
        tmp_path,
    )
    runner = SchedulerRunner(home=tmp_path)
    delivered: list[tuple[str, str]] = []

    async def turn_fn(inbound):
        raise AssertionError("message jobs must skip LLM")

    async def send_fn(channel: str, outbound: Outbound) -> None:
        delivered.append((channel, outbound.text))

    outs = await runner.run_once(turn_fn, send_fn=send_fn)
    assert outs == ["Journal now"]
    assert delivered == [("telegram", "Journal now")]
    assert load_jobs(tmp_path)[0].enabled is False


def test_cron_respects_job_timezone() -> None:
    # 22:10 Asia/Bangkok == 15:10 UTC
    utc_now = datetime(2026, 9, 10, 15, 10, tzinfo=UTC)
    job = Job(
        id="tz",
        prompt="x",
        schedule="10 22 * * *",
        timezone="Asia/Bangkok",
        enabled=True,
    )
    assert is_job_due(job, utc_now)
    assert not is_job_due(job, utc_now + timedelta(minutes=1))


def test_is_job_due_run_at_boundary() -> None:
    when = datetime(2026, 9, 10, 15, 10, tzinfo=UTC)
    job = Job(
        id="j",
        prompt="x",
        message="m",
        schedule="@once",
        run_at=when.isoformat(),
        timezone="UTC",
    )
    assert not is_job_due(job, when - timedelta(seconds=1))
    assert is_job_due(job, when)


def test_schedule_add_naive_run_at_uses_config_timezone(tmp_path: Path) -> None:
    from lattice.timeutil import persist_timezone

    persist_timezone("Asia/Ho_Chi_Minh", tmp_path)
    msg = schedule_add(
        reminder="Night routine",
        home=tmp_path,
        run_at="2026-09-10T22:45:00",
        deliver="telegram",
        job_id="night-1",
    )
    assert "2026-09-10T22:45:00+07:00" in msg
    jobs = load_jobs(tmp_path)
    assert jobs[0].timezone == "Asia/Ho_Chi_Minh"
    assert jobs[0].run_at == "2026-09-10T22:45:00+07:00"
    # Local 22:45+07 is due at 15:45 UTC
    assert is_job_due(jobs[0], datetime(2026, 9, 10, 15, 45, tzinfo=UTC))
    assert not is_job_due(jobs[0], datetime(2026, 9, 10, 15, 44, tzinfo=UTC))


def test_timezone_set_and_get(tmp_path: Path) -> None:
    from lattice.scheduler.tools import timezone_get, timezone_set

    assert "timezone saved" in timezone_set("Asia/Jakarta", home=tmp_path)
    assert "Asia/Jakarta" in timezone_get(home=tmp_path)
    assert (tmp_path / "lattice.yaml").is_file()


def test_models_come_from_lattice_yaml(tmp_path: Path) -> None:
    from lattice.config import load_settings, merge_yaml_into
    from lattice.providers.settings import resolve_model_id

    merge_yaml_into(
        tmp_path / "lattice.yaml",
        {
            "agent": {
                "primary_model": "deepseek/deepseek-v4.1-flash",
            },
        },
    )
    settings = load_settings(tmp_path)
    assert resolve_model_id(settings) == "deepseek/deepseek-v4.1-flash"


def test_legacy_agent_model_alias(tmp_path: Path) -> None:
    from lattice.config import load_settings, merge_yaml_into
    from lattice.providers.settings import resolve_model_id

    merge_yaml_into(tmp_path / "lattice.yaml", {"agent": {"model": "legacy/model"}})
    settings = load_settings(tmp_path)
    assert resolve_model_id(settings) == "legacy/model"


def test_session_dicts_to_history() -> None:
    from pydantic_ai.messages import CachePoint, ModelRequest, SystemPromptPart, UserPromptPart

    from lattice.session_history import session_dicts_to_history

    hist = session_dicts_to_history(
        [
            {"role": "system", "content": "compressed summary"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "tool", "content": "ignored"},
        ]
    )
    # system round-trips (compression summary), tool is still dropped
    assert len(hist) == 3
    system_part = hist[0].parts[0]
    assert isinstance(system_part, SystemPromptPart)
    assert system_part.content == "compressed summary"

    plain = session_dicts_to_history([{"role": "user", "content": "hi"}])
    plain_part = plain[0].parts[0]
    assert isinstance(plain_part, UserPromptPart)
    assert plain_part.content == "hi"

    cached = session_dicts_to_history(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "tool", "content": "ignored"},
        ],
        cache_boundary=True,
        cache_ttl="1h",
    )
    user_part = next(
        p
        for m in cached
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    )
    assert isinstance(user_part.content, list)
    assert isinstance(user_part.content[-1], CachePoint)
    assert user_part.content[-1].ttl == "1h"
