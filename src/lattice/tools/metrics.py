"""Personal metrics time-series store (habit/focus/mood/etc.)."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from lattice.paths import lattice_home

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_\-./]{0,63}$")

_SCHEMA = """
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


def metrics_db_path(home: Path | None = None) -> Path:
    root = (home or lattice_home()) / "metrics"
    root.mkdir(parents=True, exist_ok=True)
    return root / "metrics.db"


def _parse_when(raw: str | None) -> str:
    if not raw or not str(raw).strip():
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(raw).strip()
    # Accept date-only or full ISO; normalize to UTC Z.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00Z"
    if text.endswith("Z"):
        return text
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError(f"invalid timestamp {raw!r}; use ISO date/time") from exc


def _day(iso: str) -> date:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()


async def _connect(home: Path | None = None) -> aiosqlite.Connection:
    path = metrics_db_path(home)
    conn = await aiosqlite.connect(path)
    await conn.executescript(_SCHEMA)
    await conn.commit()
    return conn


async def metric_log(
    name: str,
    value: float,
    *,
    unit: str | None = None,
    tags: str | None = None,
    note: str | None = None,
    at: str | None = None,
    home: Path | None = None,
) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        return (
            "metric_log error: name must be 1–64 chars, start with a letter, "
            "and use only letters/digits/_-./"
        )
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "metric_log error: value must be numeric"
    tag_json: str | None = None
    if tags is not None and str(tags).strip():
        raw = str(tags).strip()
        try:
            parsed = json.loads(raw) if raw.startswith("{") else {"label": raw}
            if not isinstance(parsed, dict):
                return "metric_log error: tags must be a JSON object or short label"
            tag_json = json.dumps(parsed, sort_keys=True)
        except json.JSONDecodeError:
            tag_json = json.dumps({"label": raw})
    try:
        logged_at = _parse_when(at)
    except ValueError as exc:
        return f"metric_log error: {exc}"

    conn = await _connect(home)
    try:
        cur = await conn.execute(
            "INSERT INTO metrics(name, value, unit, tags, note, logged_at) VALUES (?,?,?,?,?,?)",
            (name, numeric, unit, tag_json, note, logged_at),
        )
        await conn.commit()
        row_id = cur.lastrowid
    finally:
        await conn.close()
    return (
        f"logged metric id={row_id} name={name} value={numeric}"
        + (f" unit={unit}" if unit else "")
        + f" at={logged_at}"
    )


def _streak(days_with_data: set[date], *, today: date | None = None) -> int:
    """Count consecutive days ending at today (or most recent day with data)."""
    if not days_with_data:
        return 0
    anchor = today or max(days_with_data)
    # If the latest activity isn't today or yesterday, streak is 0 for "active" sense
    # when today is provided and gap > 1 — but for reports use max day as anchor.
    streak = 0
    cursor = anchor
    while cursor in days_with_data:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def metric_query(
    name: str | None = None,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
    home: Path | None = None,
) -> str:
    """Query metrics; when ``name`` is set, include summary stats + streak."""
    limit = max(1, min(int(limit or 200), 2000))
    clauses: list[str] = []
    params: list[Any] = []
    if name:
        name = name.strip()
        if not _NAME_RE.match(name):
            return "metric_query error: invalid name"
        clauses.append("name = ?")
        params.append(name)
    if since:
        try:
            clauses.append("logged_at >= ?")
            params.append(_parse_when(since))
        except ValueError as exc:
            return f"metric_query error: {exc}"
    if until:
        try:
            # Inclusive end-of-day for date-only
            end = _parse_when(until)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", (until or "").strip()):
                end = (until.strip()) + "T23:59:59Z"
            clauses.append("logged_at <= ?")
            params.append(end)
        except ValueError as exc:
            return f"metric_query error: {exc}"

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = await _connect(home)
    try:
        cur = await conn.execute(
            f"SELECT id, name, value, unit, tags, note, logged_at FROM metrics{where} "
            "ORDER BY logged_at DESC LIMIT ?",
            [*params, limit],
        )
        rows = await cur.fetchall()
        summary_lines: list[str] = []
        if name:
            cur2 = await conn.execute(
                f"SELECT COUNT(*), AVG(value), SUM(value), MIN(value), MAX(value) "
                f"FROM metrics{where}",
                params,
            )
            count, avg, total, vmin, vmax = await cur2.fetchone()
            cur3 = await conn.execute(
                f"SELECT DISTINCT substr(logged_at, 1, 10) FROM metrics{where}",
                params,
            )
            day_rows = await cur3.fetchall()
            days = {date.fromisoformat(r[0]) for r in day_rows if r[0]}
            today = datetime.now(UTC).date()
            active = (
                _streak(days, today=today)
                if today in days or (today - timedelta(days=1)) in days
                else 0
            )
            # Also report streak ending at last activity day
            last_streak = _streak(days) if days else 0
            # Weekly totals (ISO week)
            cur4 = await conn.execute(
                f"SELECT substr(logged_at, 1, 10), SUM(value) FROM metrics{where} "
                "GROUP BY substr(logged_at, 1, 10) ORDER BY 1 DESC LIMIT 28",
                params,
            )
            by_day = await cur4.fetchall()

            def _fmt(v: float | None) -> str:
                return f"{v:.4g}" if v is not None else "n/a"

            summary_lines = [
                f"summary name={name} count={count or 0} "
                f"avg={_fmt(avg)} sum={_fmt(total)} min={_fmt(vmin)} max={_fmt(vmax)}",
                f"streak_active_days={active} streak_from_last={last_streak} "
                f"distinct_days={len(days)}",
            ]
            if by_day:
                summary_lines.append("by_day (latest ≤28):")
                for d, s in by_day:
                    summary_lines.append(f"  {d}\t{s}")
    finally:
        await conn.close()

    if not rows and not summary_lines:
        return "(no metrics)"
    lines = list(summary_lines)
    if summary_lines:
        lines.append("rows:")
    lines.append("id\tname\tvalue\tunit\ttags\tnote\tlogged_at")
    for row in rows:
        rid, n, val, unit, tags, note, logged_at = row
        lines.append(
            "\t".join(
                "" if v is None else str(v) for v in (rid, n, val, unit, tags, note, logged_at)
            )
        )
    return "\n".join(lines)[:100_000]
