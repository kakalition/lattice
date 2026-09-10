"""Scheduler jobs and runner."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lattice.models import Inbound, Outbound
from lattice.paths import lattice_home

logger = logging.getLogger("lattice.scheduler")


@dataclass
class Job:
    id: str
    prompt: str
    profile: str = "default"
    deliver: str = "none"  # telegram|cli|none
    schedule: str = ""  # cron-like: five fields, or "@once" / "once"
    preapproved_tools: list[str] = field(default_factory=list)
    enabled: bool = True
    timezone: str = "UTC"
    last_run: str | None = None
    run_at: str | None = None  # absolute ISO one-shot; preferred over cron when set
    message: str | None = None  # if set, deliver directly (skip LLM turn)


def jobs_path(home: Path | None = None) -> Path:
    return (home or lattice_home()) / "scheduler" / "jobs.json"


def load_jobs(home: Path | None = None) -> list[Job]:
    path = jobs_path(home)
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    jobs: list[Job] = []
    for item in raw.get("jobs") or []:
        jobs.append(Job(**{k: v for k, v in item.items() if k in Job.__dataclass_fields__}))
    return jobs


def save_jobs(jobs: list[Job], home: Path | None = None) -> None:
    path = jobs_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "jobs": [
            {
                "id": j.id,
                "prompt": j.prompt,
                "profile": j.profile,
                "deliver": j.deliver,
                "schedule": j.schedule,
                "preapproved_tools": j.preapproved_tools,
                "enabled": j.enabled,
                "timezone": j.timezone,
                "last_run": j.last_run,
                "run_at": j.run_at,
                "message": j.message,
            }
            for j in jobs
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def job_to_inbound(job: Job, *, user_id: str = "scheduler") -> Inbound:
    return Inbound(
        text=job.prompt,
        profile_id=job.profile,
        user_id=user_id,
        channel="scheduler",
    )


def _parse_last_run(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_run_at(value: str | None, *, timezone: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(timezone))
        except ZoneInfoNotFoundError:
            dt = dt.replace(tzinfo=UTC)
    return dt


def job_local_now(job: Job, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    try:
        return now.astimezone(ZoneInfo(job.timezone or "UTC"))
    except ZoneInfoNotFoundError:
        return now.astimezone(UTC)


def cron_field_matches(field: str, value: int) -> bool:
    """Match a single cron field (minute/hour/dom/month/dow). Supports *, N, */N, A-B, lists."""
    field = field.strip()
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if part.startswith("*/"):
            try:
                step = int(part[2:])
            except ValueError:
                continue
            if step > 0 and value % step == 0:
                return True
            continue
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                if int(lo) <= value <= int(hi):
                    return True
            except ValueError:
                continue
            continue
        try:
            if int(part) == value:
                return True
        except ValueError:
            continue
    return False


def cron_matches(expr: str, when: datetime) -> bool:
    """Five-field cron: minute hour day-of-month month day-of-week (0=Sun or 7=Sun)."""
    expr = expr.strip()
    if expr in {"@once", "once"}:
        return True  # caller must gate on last_run is None
    parts = expr.split()
    if len(parts) != 5:
        # Non-empty unknown schedule: treat as always-due (legacy lite behavior)
        return bool(expr)
    minute, hour, dom, month, dow = parts
    # Python weekday: Mon=0 … Sun=6 → cron Sun=0
    cron_dow = (when.weekday() + 1) % 7
    return (
        cron_field_matches(minute, when.minute)
        and cron_field_matches(hour, when.hour)
        and cron_field_matches(dom, when.day)
        and cron_field_matches(month, when.month)
        and (
            cron_field_matches(dow, cron_dow)
            or cron_field_matches(dow, 7 if cron_dow == 0 else cron_dow)
        )
    )


def is_job_due(
    job: Job, now: datetime | None = None, *, min_interval: timedelta = timedelta(seconds=55)
) -> bool:
    if not job.enabled:
        return False
    now_utc = now or datetime.now(UTC)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    last = _parse_last_run(job.last_run)

    run_at = _parse_run_at(job.run_at, timezone=job.timezone)
    if run_at is not None:
        return last is None and now_utc >= run_at.astimezone(UTC)

    if not job.schedule.strip():
        return False
    sched = job.schedule.strip()
    if sched in {"@once", "once"}:
        return last is None
    if last and now_utc - last.astimezone(UTC) < min_interval:
        return False  # avoid double-fire within gateway tick window
    local = job_local_now(job, now_utc)
    return cron_matches(sched, local)


@dataclass
class SchedulerRunner:
    home: Path | None = None

    def due_jobs(self, now: datetime | None = None) -> list[Job]:
        now = now or datetime.now(UTC)
        due = [j for j in load_jobs(self.home) if is_job_due(j, now)]
        if due:
            logger.info("due jobs: %s", ", ".join(j.id for j in due))
        return due

    def mark_run(self, job_id: str) -> None:
        jobs = load_jobs(self.home)
        for job in jobs:
            if job.id == job_id:
                job.last_run = datetime.now(UTC).isoformat()
                # one-shots disable after first run
                if job.run_at or job.schedule.strip() in {"@once", "once"}:
                    job.enabled = False
        save_jobs(jobs, self.home)

    async def run_once(self, run_turn_fn: Any, send_fn: Any | None = None) -> list[str]:
        results: list[str] = []
        for job in self.due_jobs():
            logger.info(
                "running job id=%s deliver=%s run_at=%s schedule=%s",
                job.id,
                job.deliver,
                job.run_at,
                job.schedule,
            )
            try:
                if job.message:
                    outbound = Outbound(text=job.message, profile_id=job.profile)
                else:
                    outbound = await run_turn_fn(job_to_inbound(job))
                results.append(outbound.text)
                if send_fn and job.deliver != "none":
                    await send_fn(job.deliver, outbound)
                    logger.info("delivered job %s via %s", job.id, job.deliver)
                elif job.deliver != "none" and not send_fn:
                    logger.error("job %s deliver=%s but no send_fn bound", job.id, job.deliver)
            except Exception as exc:
                msg = f"scheduler job {job.id} failed: {exc}"
                logger.exception(msg)
                results.append(msg)
                if send_fn and job.deliver != "none":
                    await send_fn(job.deliver, Outbound(text=msg))
            self.mark_run(job.id)
        return results
