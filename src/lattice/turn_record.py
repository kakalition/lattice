"""Structured per-turn JSONL records.

The human log (``logging_config``) answers "what happened"; this answers
"how much / how fast / how often" without parsing prose. One JSON object per
line is appended to ``<home>/logs/turns.jsonl`` and is deliberately
best-effort: a write failure must never fail a turn.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("lattice.turn_record")


class TurnOutcome(StrEnum):
    COMPLETED = "completed"
    BUDGET = "budget"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ERROR = "error"
    EMPTY = "empty"


class ToolRecord(BaseModel):
    name: str
    duration_ms: int
    ok: bool
    result_bytes: int
    truncated: bool


class ContextRecord(BaseModel):
    before_messages: int = 0
    after_messages: int = 0
    compressed: bool = False
    used_trim_fallback: bool = False


class TurnRecord(BaseModel):
    """One line in ``turns.jsonl``."""

    turn_id: str
    started_at: str
    ended_at: str
    duration_ms: int
    channel: str | None = None
    user_id: str | None = None
    profile_id: str | None = None
    session_id: str | None = None
    model: str | None = None
    outcome: str = TurnOutcome.COMPLETED.value
    error_kind: str | None = None
    phases: dict[str, int] = Field(default_factory=dict)
    tool_ms: int = 0
    tools_offered: int = 0
    tools: list[ToolRecord] = Field(default_factory=list)
    retry_count: int = 0
    ttft_ms: int | None = None
    stream_chunks: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    context: ContextRecord = Field(default_factory=ContextRecord)


def turn_records_path(home: Path) -> Path:
    return home / "logs" / "turns.jsonl"


def append_turn_record(record: TurnRecord, home: Path) -> None:
    """Append one JSON line; caller is responsible for suppressing failures."""
    path = turn_records_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(record.model_dump_json() + "\n")
