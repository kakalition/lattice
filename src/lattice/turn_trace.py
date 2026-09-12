"""Complete turn tracing to the process log.

Two sinks live here:
- the human log line per event (what happened), and
- an accumulated :class:`TurnRecord` written as one JSON line per turn.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lattice.deps import result_failed
from lattice.events import NullTurnEvents, TurnEvents
from lattice.turn_record import (
    ContextRecord,
    ToolRecord,
    TurnOutcome,
    TurnRecord,
    append_turn_record,
)

logger = logging.getLogger("lattice.turn")

# Current turn id for the running task, so model-level logging can be correlated
# back to a turn without threading the id through every call signature.
_turn_id_var: ContextVar[str | None] = ContextVar("lattice_turn_id", default=None)


def current_turn_id() -> str | None:
    return _turn_id_var.get()


def new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


def _clip(value: Any, limit: int = 800) -> str:
    text = value if isinstance(value, str) else repr(value)
    text = text.replace("\n", "\\n")
    if len(text) > limit:
        return text[:limit] + f"…(+{len(text) - limit}c)"
    return text


def _clip_args(args: dict[str, Any], limit: int = 400) -> dict[str, str]:
    return {k: _clip(v, limit) for k, v in args.items()}


def _result_ok(result: str) -> bool:
    return not result_failed(result)


class LoggingTurnEvents:
    """Logs every turn event; optionally fans out to another TurnEvents sink."""

    def __init__(
        self,
        turn_id: str,
        *,
        inner: TurnEvents | None = None,
        home: Path | None = None,
        record_enabled: bool = True,
        on_tool_start_hook: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.turn_id = turn_id
        self.inner: TurnEvents = inner or NullTurnEvents()
        self.home = home
        self.record_enabled = record_enabled
        self.on_tool_start_hook = on_tool_start_hook
        self.started = time.monotonic()
        self.started_at = datetime.now(UTC)
        self.tool_calls = 0
        self.tools_offered = 0
        self.skills_used: list[str] = []
        # Accumulated per-phase wall time, reported on the END line so the
        # routing latency split is measured rather than guessed.
        self.phases: dict[str, float] = {}
        # Per-tool timing/completeness for the structured record.
        self.tools: list[ToolRecord] = []
        # name -> FIFO of (start_monotonic, clipped args); paired with results
        # in order so concurrent same-name calls keep their own operands.
        self._tool_starts: dict[str, list[tuple[float, dict[str, str]]]] = {}
        self.ttft_ms: int | None = None
        self.stream_chunks = 0
        self.retry_count = 0
        self.outcome: str = TurnOutcome.COMPLETED.value
        self.error_kind: str | None = None
        self.context = ContextRecord()
        self.channel: str | None = None
        self.user_id: str | None = None
        self.profile_id: str | None = None
        self.session_id: str | None = None
        self.model: str | None = None

    @contextmanager
    def timed(self, phase: str) -> Iterator[None]:
        start = time.monotonic()
        try:
            yield
        finally:
            self.phases[phase] = self.phases.get(phase, 0.0) + (time.monotonic() - start)

    def _p(self, msg: str, *args: Any) -> None:
        logger.info("turn=%s " + msg, self.turn_id, *args)

    async def on_status(self, message: str) -> None:
        self._p("status: %s", message)
        await self.inner.on_status(message)

    async def on_stream_delta(self, text: str) -> None:
        # Final reply is logged explicitly in run_turn; skip streaming spam.
        if text:
            self.stream_chunks += 1
            if self.ttft_ms is None:
                self.ttft_ms = int((time.monotonic() - self.started) * 1000)
        await self.inner.on_stream_delta(text)

    async def on_tool_start(self, name: str, args: dict[str, Any]) -> None:
        if self.on_tool_start_hook is not None:
            # Best-effort; a hook failure must never abort the tool call.
            with suppress(Exception):
                await self.on_tool_start_hook(name)
        self.tool_calls += 1
        self._tool_starts.setdefault(name, []).append((time.monotonic(), _clip_args(args)))
        if name == "skill_view":
            skill = str(args.get("name") or args.get("skill") or "")
            if skill:
                self.skills_used.append(skill)
                self._p("skill_load: %s", skill)
        self._p("tool_start: %s args=%s", name, _clip_args(args))
        await self.inner.on_tool_start(name, args)

    async def on_tool_end(self, name: str, result: str) -> None:
        duration_ms = 0
        args: dict[str, str] = {}
        starts = self._tool_starts.get(name)
        if starts:
            # FIFO: pair concurrent same-name calls with their results in order.
            started, args = starts.pop(0)
            duration_ms = int((time.monotonic() - started) * 1000)
        self.tools.append(
            ToolRecord(
                name=name,
                duration_ms=duration_ms,
                ok=_result_ok(result),
                result_bytes=len(result),
                truncated="[truncated]" in result,
                args=args,
            )
        )
        self._p("tool_end: %s tool_ms=%d result=%s", name, duration_ms, _clip(result, 1200))
        await self.inner.on_tool_end(name, result)

    def log_begin(
        self,
        *,
        channel: str,
        user_id: str,
        profile_id: str,
        session_id: str,
        inbound_text: str,
        tools: list[str],
        skills: list[tuple[str, str]],
        model: str | None = None,
    ) -> None:
        _turn_id_var.set(self.turn_id)
        self.channel = channel
        self.user_id = user_id
        self.profile_id = profile_id
        self.session_id = session_id
        self.model = model
        self.tools_offered = len(tools)
        self._p(
            "BEGIN channel=%s user=%s profile=%s session=%s",
            channel,
            user_id,
            profile_id,
            session_id,
        )
        self._p("inbound: %s", _clip(inbound_text, 2000))
        self._p("tools_offered (%d): %s", len(tools), ", ".join(tools) or "(none)")
        skill_names = [n for n, _ in skills]
        self._p(
            "skills_offered (%d): %s",
            len(skill_names),
            ", ".join(skill_names) or "(none)",
        )

    def set_outcome(self, outcome: TurnOutcome | str, *, error_kind: str | None = None) -> None:
        self.outcome = str(outcome)
        self.error_kind = error_kind

    def set_retries(self, retries: int) -> None:
        self.retry_count = retries

    def set_context(self, context: ContextRecord) -> None:
        self.context = context

    def _build_record(self, *, duration_ms: int) -> TurnRecord:
        return TurnRecord(
            turn_id=self.turn_id,
            started_at=self.started_at.isoformat(),
            ended_at=datetime.now(UTC).isoformat(),
            duration_ms=duration_ms,
            channel=self.channel,
            user_id=self.user_id,
            profile_id=self.profile_id,
            session_id=self.session_id,
            model=self.model,
            outcome=self.outcome,
            error_kind=self.error_kind,
            phases={name: int(seconds * 1000) for name, seconds in self.phases.items()},
            tool_ms=sum(t.duration_ms for t in self.tools),
            tools_offered=self.tools_offered,
            tools=self.tools,
            retry_count=self.retry_count,
            ttft_ms=self.ttft_ms,
            stream_chunks=self.stream_chunks,
            context=self.context,
        )

    def log_end(
        self,
        *,
        outbound_text: str,
        error: str | None = None,
        usage: dict[str, Any] | None = None,
        timings: Mapping[str, float] | None = None,
        outcome: TurnOutcome | str | None = None,
        retries: int | None = None,
        context: ContextRecord | None = None,
        error_kind: str | None = None,
    ) -> None:
        if outcome is not None:
            self.outcome = str(outcome)
        if retries is not None:
            self.retry_count = retries
        if context is not None:
            self.context = context
        if error_kind is not None:
            self.error_kind = error_kind
        ms = int((time.monotonic() - self.started) * 1000)
        if error:
            self._p("ERROR after %dms: %s", ms, error)
        self._p("outbound: %s", _clip(outbound_text, 4000))
        phases = self.phases if timings is None else timings
        timing_suffix = "".join(f" {name}_ms={int(phases[name] * 1000)}" for name in phases)
        ttft = f" ttft_ms={self.ttft_ms}" if self.ttft_ms is not None else ""
        self._p(
            "END duration_ms=%d tool_calls=%d tool_ms=%d outcome=%s retries=%d%s%s skills_used=%s",
            ms,
            self.tool_calls,
            sum(t.duration_ms for t in self.tools),
            self.outcome,
            self.retry_count,
            timing_suffix,
            ttft,
            ",".join(self.skills_used) or "(none)",
        )
        if usage:
            cost = usage.get("cost")
            self._p(
                "usage: model=%s input=%s output=%s cache_read=%s cache_write=%s "
                "cache_hit_ratio=%.3f requests=%s cost=%s",
                usage.get("model", "?"),
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                usage.get("cache_read_tokens", 0),
                usage.get("cache_write_tokens", 0),
                float(usage.get("cache_hit_ratio", 0.0) or 0.0),
                usage.get("requests", 0),
                f"{float(cost):.6f}" if cost is not None else "-",
            )
        self._write_record(ms, usage or {})

    def _write_record(self, duration_ms: int, usage: dict[str, Any]) -> None:
        if self.home is None or not self.record_enabled:
            return
        # Observability is best-effort: a bad record must never fail a turn.
        try:
            record = self._build_record(duration_ms=duration_ms)
            record.usage = usage
            append_turn_record(record, self.home)
        except Exception:
            logger.warning("failed to write turn record for turn=%s", self.turn_id, exc_info=True)
