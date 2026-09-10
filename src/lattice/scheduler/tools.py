"""Scheduler job helpers callable from agent tools."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lattice.scheduler.jobs import Job, load_jobs, save_jobs


def local_tz_name() -> str:
    tz = datetime.now().astimezone().tzinfo
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        return key
    return "UTC"


def _parse_run_at(value: str, *, timezone: str) -> datetime:
    raw = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(timezone))
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone {timezone!r}") from exc
    return dt


def schedule_add(
    *,
    reminder: str,
    home: Path | None = None,
    run_at: str = "",
    cron: str = "",
    timezone: str = "",
    deliver: str = "telegram",
    profile: str = "default",
    job_id: str = "",
) -> str:
    """Create a one-shot (run_at) or recurring (cron) reminder job."""
    reminder = reminder.strip()
    if not reminder:
        return "reminder text is required"
    tz = (timezone or local_tz_name()).strip()
    run_at = run_at.strip()
    cron = cron.strip()
    if bool(run_at) == bool(cron):
        return "provide exactly one of run_at (ISO-8601) or cron (five fields)"
    if deliver not in {"telegram", "cli", "none"}:
        return "deliver must be telegram|cli|none"

    jid = job_id.strip() or f"job-{uuid.uuid4().hex[:10]}"
    jobs = load_jobs(home)
    if any(j.id == jid for j in jobs):
        return f"job id already exists: {jid}"

    schedule = cron if cron else "@once"
    run_at_iso: str | None = None
    if run_at:
        try:
            run_at_iso = _parse_run_at(run_at, timezone=tz).isoformat()
        except ValueError as exc:
            return f"invalid run_at: {exc}"
        schedule = "@once"
    elif not re.fullmatch(r"(\S+\s+){4}\S+", cron) and cron not in {"@once", "once"}:
        return "cron must be five fields: minute hour day-of-month month day-of-week"

    job = Job(
        id=jid,
        prompt=reminder,
        message=reminder,
        profile=profile,
        deliver=deliver,
        schedule=schedule,
        enabled=True,
        timezone=tz,
        run_at=run_at_iso,
    )
    jobs.append(job)
    save_jobs(jobs, home)
    when = run_at_iso or f"cron {schedule} ({tz})"
    return f"scheduled {jid} → deliver={deliver} at {when}"


def schedule_list(*, home: Path | None = None) -> str:
    jobs = load_jobs(home)
    if not jobs:
        return "(no scheduled jobs)"
    lines: list[str] = []
    for j in jobs:
        state = "on" if j.enabled else "off"
        when = j.run_at or j.schedule
        lines.append(
            f"{j.id} [{state}] {when} tz={j.timezone} deliver={j.deliver} :: {j.message or j.prompt}"
        )
    return "\n".join(lines)


def schedule_cancel(job_id: str, *, home: Path | None = None) -> str:
    jobs = load_jobs(home)
    keep = [j for j in jobs if j.id != job_id]
    if len(keep) == len(jobs):
        return f"not found: {job_id}"
    save_jobs(keep, home)
    return f"cancelled {job_id}"
