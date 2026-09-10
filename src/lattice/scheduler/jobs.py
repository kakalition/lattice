"""Scheduler jobs and runner."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lattice.models import Inbound
from lattice.paths import lattice_home


@dataclass
class Job:
    id: str
    prompt: str
    profile: str = "default"
    deliver: str = "none"  # telegram|cli|none
    schedule: str = ""  # cron-like expression (stored; lite matching)
    preapproved_tools: list[str] = field(default_factory=list)
    enabled: bool = True
    timezone: str = "UTC"
    last_run: str | None = None


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


@dataclass
class SchedulerRunner:
    home: Path | None = None

    def due_jobs(self, now: datetime | None = None) -> list[Job]:
        """Lite: jobs with empty schedule are manual; cron matching deferred to interval tick markers."""
        _ = now or datetime.now(UTC)
        return [j for j in load_jobs(self.home) if j.enabled and j.schedule]

    def mark_run(self, job_id: str) -> None:
        jobs = load_jobs(self.home)
        for job in jobs:
            if job.id == job_id:
                job.last_run = datetime.now(UTC).isoformat()
        save_jobs(jobs, self.home)

    async def run_once(self, run_turn_fn: Any, send_fn: Any | None = None) -> list[str]:
        results: list[str] = []
        for job in self.due_jobs():
            inbound = job_to_inbound(job)
            try:
                outbound = await run_turn_fn(inbound)
                results.append(outbound.text)
                if send_fn and job.deliver != "none":
                    await send_fn(job.deliver, outbound)
            except Exception as exc:
                msg = f"scheduler job {job.id} failed: {exc}"
                results.append(msg)
                if send_fn and job.deliver != "none":
                    from lattice.models import Outbound

                    await send_fn(job.deliver, Outbound(text=msg))
            self.mark_run(job.id)
        return results
