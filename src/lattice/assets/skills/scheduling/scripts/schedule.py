#!/usr/bin/env python3
"""Scheduled reminder jobs (stdlib only).

Bundled with the `scheduling` skill; run through Lattice's ``execute_script``.
Writes ``$LATTICE_HOME/scheduler/jobs.json`` in the shape the gateway runner reads.

Usage:
  schedule.py add --reminder TEXT (--run-at ISO | --cron "M H DOM MON DOW")
                 [--timezone TZ] [--deliver telegram|cli|none] [--job-id ID]
  schedule.py list
  schedule.py cancel JOB_ID
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

DELIVER = ("telegram", "cli", "none")
CRON_RE = re.compile(r"(\S+\s+){4}\S+")


def home_dir() -> Path:
    env = os.environ.get("LATTICE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / ".lattice"


def jobs_path() -> Path:
    return home_dir() / "scheduler" / "jobs.json"


def resolve_timezone(explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    env = os.environ.get("LATTICE_TIMEZONE") or os.environ.get("TZ")
    if env and env.strip():
        return env.strip()
    tz = datetime.now().astimezone().tzinfo
    key = getattr(tz, "key", None)
    return str(key) if key else "UTC"


def load_jobs() -> list[dict]:
    path = jobs_path()
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [j for j in (raw.get("jobs") or []) if isinstance(j, dict)]


def save_jobs(jobs: list[dict]) -> None:
    path = jobs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"jobs": jobs}, indent=2), encoding="utf-8")


def parse_run_at(value: str, timezone: str) -> datetime:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    raw = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(timezone))
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone {timezone!r}") from exc
    return dt


def cmd_add(args: argparse.Namespace) -> int:
    reminder = (args.reminder or "").strip()
    if not reminder:
        print("reminder text is required", file=sys.stderr)
        return 1
    tz = resolve_timezone(args.timezone or "")
    run_at = (args.run_at or "").strip()
    cron = (args.cron or "").strip()
    if bool(run_at) == bool(cron):
        print("provide exactly one of --run-at (ISO-8601) or --cron (five fields)", file=sys.stderr)
        return 1
    deliver = (args.deliver or "telegram").strip()
    if deliver not in DELIVER:
        print("deliver must be telegram|cli|none", file=sys.stderr)
        return 1

    job_id = (args.job_id or "").strip() or f"job-{uuid.uuid4().hex[:10]}"
    jobs = load_jobs()
    if any(j.get("id") == job_id for j in jobs):
        print(f"job id already exists: {job_id}", file=sys.stderr)
        return 1

    schedule = cron if cron else "@once"
    run_at_iso: str | None = None
    if run_at:
        try:
            run_at_iso = parse_run_at(run_at, tz).isoformat()
        except ValueError as exc:
            print(f"invalid run_at: {exc}", file=sys.stderr)
            return 1
    elif not CRON_RE.fullmatch(cron) and cron not in {"@once", "once"}:
        print(
            "cron must be five fields: minute hour day-of-month month day-of-week", file=sys.stderr
        )
        return 1

    profile = (args.profile or os.environ.get("LATTICE_PROFILE") or "default").strip()
    jobs.append(
        {
            "id": job_id,
            "prompt": reminder,
            "profile": profile,
            "deliver": deliver,
            "schedule": schedule,
            "preapproved_tools": [],
            "enabled": True,
            "timezone": tz,
            "last_run": None,
            "run_at": run_at_iso,
            "message": reminder,
        }
    )
    save_jobs(jobs)
    when = run_at_iso or f"cron {schedule} ({tz})"
    print(f"scheduled {job_id} -> deliver={deliver} at {when}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    jobs = load_jobs()
    if not jobs:
        print("(no scheduled jobs)")
        return 0
    for job in jobs:
        state = "on" if job.get("enabled") else "off"
        when = job.get("run_at") or job.get("schedule") or ""
        print(
            f"{job.get('id')} [{state}] {when} tz={job.get('timezone')} "
            f"deliver={job.get('deliver')} :: {job.get('message') or job.get('prompt')}"
        )
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    jobs = load_jobs()
    keep = [j for j in jobs if j.get("id") != args.job_id]
    if len(keep) == len(jobs):
        print(f"not found: {args.job_id}", file=sys.stderr)
        return 1
    save_jobs(keep)
    print(f"cancelled {args.job_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="schedule", description="Lattice scheduled reminders")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="create a one-shot or recurring reminder")
    add.add_argument("--reminder", required=True)
    add.add_argument("--run-at", dest="run_at", default="")
    add.add_argument("--cron", default="")
    add.add_argument("--timezone", default="")
    add.add_argument("--deliver", default="telegram")
    add.add_argument("--job-id", dest="job_id", default="")
    add.add_argument("--profile", default="")
    add.set_defaults(func=cmd_add)

    sub.add_parser("list", help="list jobs").set_defaults(func=cmd_list)

    cancel = sub.add_parser("cancel", help="cancel a job by id")
    cancel.add_argument("job_id")
    cancel.set_defaults(func=cmd_cancel)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"schedule error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
