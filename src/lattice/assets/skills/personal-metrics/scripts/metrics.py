#!/usr/bin/env python3
"""Personal metrics time-series store (stdlib only).

Bundled with the `personal-metrics` skill; run through Lattice's
``execute_script`` (system interpreter, no third-party imports).

Usage:
  metrics.py log NAME VALUE [--unit U] [--tags TAGS] [--note N] [--at ISO]
  metrics.py query [NAME] [--since ISO|DATE] [--until ISO|DATE] [--limit N]

DB: ``$LATTICE_HOME/metrics/metrics.db``
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_\-./]{0,63}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT,
  tags TEXT,
  note TEXT,
  logged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_name_time ON metrics(name, logged_at);
"""


def home_dir() -> Path:
    env = os.environ.get("LATTICE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / ".lattice"


def db_path() -> Path:
    root = home_dir() / "metrics"
    root.mkdir(parents=True, exist_ok=True)
    return root / "metrics.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def parse_when(raw: str | None) -> str:
    if not raw or not str(raw).strip():
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(raw).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00Z"
    if text.endswith("Z"):
        return text
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_of(iso: str) -> date:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()


def streak(days: set[date], *, today: date | None = None) -> int:
    if not days:
        return 0
    anchor = today or max(days)
    count = 0
    cursor = anchor
    while cursor in days:
        count += 1
        cursor -= timedelta(days=1)
    return count


def parse_tags(raw: str | None) -> tuple[str | None, str | None]:
    if raw is None or not str(raw).strip():
        return None, None
    text = str(raw).strip()
    try:
        parsed = json.loads(text) if text.startswith("{") else {"label": text}
        if not isinstance(parsed, dict):
            return None, "tags must be a JSON object or short label"
        return json.dumps(parsed, sort_keys=True), None
    except json.JSONDecodeError:
        return json.dumps({"label": text}), None


def cmd_log(args: argparse.Namespace) -> int:
    name = (args.name or "").strip()
    if not NAME_RE.match(name):
        print(
            "metric_log error: name must be 1-64 chars, start with a letter, "
            "and use only letters/digits/_-./",
            file=sys.stderr,
        )
        return 1
    try:
        value = float(args.value)
    except (TypeError, ValueError):
        print("metric_log error: value must be numeric", file=sys.stderr)
        return 1
    tag_json, err = parse_tags(args.tags)
    if err:
        print(f"metric_log error: {err}", file=sys.stderr)
        return 1
    try:
        logged_at = parse_when(args.at)
    except ValueError as exc:
        print(f"metric_log error: {exc}", file=sys.stderr)
        return 1

    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO metrics(name, value, unit, tags, note, logged_at) VALUES (?,?,?,?,?,?)",
            (name, value, args.unit, tag_json, args.note, logged_at),
        )
        conn.commit()
        row_id = cur.lastrowid
    finally:
        conn.close()
    unit = f" unit={args.unit}" if args.unit else ""
    print(f"logged metric id={row_id} name={name} value={value}{unit} at={logged_at}")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    limit = max(1, min(int(args.limit or 200), 2000))
    clauses: list[str] = []
    params: list[object] = []
    name = (args.name or "").strip()
    if name:
        if not NAME_RE.match(name):
            print("metric_query error: invalid name", file=sys.stderr)
            return 1
        clauses.append("name = ?")
        params.append(name)
    if args.since:
        clauses.append("logged_at >= ?")
        params.append(parse_when(args.since))
    if args.until:
        end = parse_when(args.until)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", (args.until or "").strip()):
            end = args.until.strip() + "T23:59:59Z"
        clauses.append("logged_at <= ?")
        params.append(end)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = connect()
    summary_lines: list[str] = []
    try:
        cur = conn.execute(
            f"SELECT id, name, value, unit, tags, note, logged_at FROM metrics{where} "
            "ORDER BY logged_at DESC LIMIT ?",
            [*params, limit],
        )
        rows = cur.fetchall()
        if name:
            row = conn.execute(
                f"SELECT COUNT(*), AVG(value), SUM(value), MIN(value), MAX(value) FROM metrics{where}",
                params,
            ).fetchone()
            count, avg, total, vmin, vmax = row
            day_rows = conn.execute(
                f"SELECT DISTINCT substr(logged_at, 1, 10) FROM metrics{where}", params
            ).fetchall()
            days = {date.fromisoformat(r[0]) for r in day_rows if r[0]}
            today = datetime.now(UTC).date()
            active = (
                streak(days, today=today)
                if today in days or (today - timedelta(days=1)) in days
                else 0
            )
            last_streak = streak(days) if days else 0
            by_day = conn.execute(
                f"SELECT substr(logged_at, 1, 10), SUM(value) FROM metrics{where} "
                "GROUP BY substr(logged_at, 1, 10) ORDER BY 1 DESC LIMIT 28",
                params,
            ).fetchall()

            def fmt(v: float | None) -> str:
                return f"{v:.4g}" if v is not None else "n/a"

            summary_lines = [
                f"summary name={name} count={count or 0} "
                f"avg={fmt(avg)} sum={fmt(total)} min={fmt(vmin)} max={fmt(vmax)}",
                f"streak_active_days={active} streak_from_last={last_streak} "
                f"distinct_days={len(days)}",
            ]
            if by_day:
                summary_lines.append("by_day (latest <=28):")
                for d, s in by_day:
                    summary_lines.append(f"  {d}\t{s}")
    finally:
        conn.close()

    if not rows and not summary_lines:
        print("(no metrics)")
        return 0
    lines = list(summary_lines)
    if summary_lines:
        lines.append("rows:")
    lines.append("id\tname\tvalue\tunit\ttags\tnote\tlogged_at")
    for rid, n, val, unit, tags, note, logged_at in rows:
        cells = (rid, n, val, unit, tags, note, logged_at)
        lines.append("\t".join("" if v is None else str(v) for v in cells))
    print("\n".join(lines)[:100_000])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="metrics", description="Lattice personal metrics")
    sub = parser.add_subparsers(dest="command", required=True)

    log = sub.add_parser("log", help="record a metric point")
    log.add_argument("name")
    log.add_argument("value")
    log.add_argument("--unit")
    log.add_argument("--tags")
    log.add_argument("--note")
    log.add_argument("--at")
    log.set_defaults(func=cmd_log)

    query = sub.add_parser("query", help="query metrics (name optional)")
    query.add_argument("name", nargs="?", default="")
    query.add_argument("--since")
    query.add_argument("--until")
    query.add_argument("--limit", type=int, default=200)
    query.set_defaults(func=cmd_query)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:
        print(f"metrics error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
