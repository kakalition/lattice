"""Aggregate the structured per-turn JSONL into operational stats."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lattice.tool_names import normalize_name
from lattice.turn_record import turn_records_path


def load_records(path: Path, *, days: int | None = None) -> list[dict[str, Any]]:
    """Read ``turns.jsonl`` lines, optionally keeping only the last ``days``."""
    if not path.is_file():
        return []
    cutoff: datetime | None = None
    if days is not None and days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=days)
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if cutoff is not None:
            ended = record.get("ended_at") or record.get("started_at")
            try:
                when = datetime.fromisoformat(str(ended))
            except (ValueError, TypeError):
                when = None
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                if when < cutoff:
                    continue
        records.append(record)
    return records


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def compute_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes: dict[str, int] = {}
    durations: list[int] = []
    ttfts: list[int] = []
    requests = 0
    input_tokens = 0
    cache_read = 0
    retries = 0
    turns_with_retries = 0
    compressions = 0
    tool_calls = 0
    failing: dict[str, int] = {}
    expensive: dict[str, int] = {}
    phase_totals: dict[str, int] = {}
    phase_values: dict[str, list[int]] = {}

    for record in records:
        outcome = str(record.get("outcome") or "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        durations.append(int(record.get("duration_ms") or 0))
        ttft = record.get("ttft_ms")
        if ttft is not None:
            ttfts.append(int(ttft))
        for name, value in (record.get("phases") or {}).items():
            ms = int(value or 0)
            phase_totals[name] = phase_totals.get(name, 0) + ms
            phase_values.setdefault(name, []).append(ms)
        usage = record.get("usage") or {}
        requests += int(usage.get("requests") or 0)
        input_tokens += int(usage.get("input_tokens") or 0)
        cache_read += int(usage.get("cache_read_tokens") or 0)
        retry_count = int(record.get("retry_count") or 0)
        retries += retry_count
        if retry_count:
            turns_with_retries += 1
        context = record.get("context") or {}
        if context.get("compressed"):
            compressions += 1
        for tool in record.get("tools") or []:
            # Historical records hold flat pre-group names; normalize so old and
            # new turns aggregate under the same canonical name.
            name = normalize_name(str(tool.get("name") or "?"))
            tool_calls += 1
            duration = int(tool.get("duration_ms") or 0)
            expensive[name] = expensive.get(name, 0) + duration
            if not tool.get("ok", True):
                failing[name] = failing.get(name, 0) + 1

    return {
        "turns": len(records),
        "outcomes": dict(sorted(outcomes.items())),
        "duration_ms": {"p50": _percentile(durations, 0.5), "p95": _percentile(durations, 0.95)},
        "ttft_ms": {"p50": _percentile(ttfts, 0.5), "p95": _percentile(ttfts, 0.95)},
        "requests": requests,
        "input_tokens": input_tokens,
        "cache_read_tokens": cache_read,
        "cache_hit_ratio": (cache_read / input_tokens) if input_tokens else 0.0,
        "tool_calls": tool_calls,
        "retries": retries,
        "turns_with_retries": turns_with_retries,
        "compressions": compressions,
        "phases": {
            name: {
                "total_ms": phase_totals.get(name, 0),
                "p50": _percentile(phase_values.get(name, []), 0.5),
                "p95": _percentile(phase_values.get(name, []), 0.95),
            }
            for name in sorted(phase_values)
        },
        "top_failing_tools": sorted(failing.items(), key=lambda kv: (-kv[1], kv[0]))[:5],
        "top_expensive_tools": sorted(expensive.items(), key=lambda kv: (-kv[1], kv[0]))[:5],
    }


def stats_for_home(home: Path, *, days: int | None = None) -> dict[str, Any]:
    return compute_stats(load_records(turn_records_path(home), days=days))
