"""Complete turn tracing to the process log."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from lattice.events import NullTurnEvents, TurnEvents

logger = logging.getLogger("lattice.turn")


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


class LoggingTurnEvents:
    """Logs every turn event; optionally fans out to another TurnEvents sink."""

    def __init__(self, turn_id: str, *, inner: TurnEvents | None = None) -> None:
        self.turn_id = turn_id
        self.inner: TurnEvents = inner or NullTurnEvents()
        self.started = time.monotonic()
        self.tool_calls = 0
        self.skills_used: list[str] = []

    def _p(self, msg: str, *args: Any) -> None:
        logger.info("turn=%s " + msg, self.turn_id, *args)

    async def on_status(self, message: str) -> None:
        self._p("status: %s", message)
        await self.inner.on_status(message)

    async def on_stream_delta(self, text: str) -> None:
        # Final reply is logged explicitly in run_turn; skip streaming spam.
        await self.inner.on_stream_delta(text)

    async def on_tool_start(self, name: str, args: dict[str, Any]) -> None:
        self.tool_calls += 1
        if name == "skill_view":
            skill = str(args.get("name") or args.get("skill") or "")
            if skill:
                self.skills_used.append(skill)
                self._p("skill_load: %s", skill)
        self._p("tool_start: %s args=%s", name, _clip_args(args))
        await self.inner.on_tool_start(name, args)

    async def on_tool_end(self, name: str, result: str) -> None:
        self._p("tool_end: %s result=%s", name, _clip(result, 1200))
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
    ) -> None:
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

    def log_end(
        self,
        *,
        outbound_text: str,
        error: str | None = None,
        usage: dict[str, Any] | None = None,
        route: str | None = None,
    ) -> None:
        ms = int((time.monotonic() - self.started) * 1000)
        if error:
            self._p("ERROR after %dms: %s", ms, error)
        self._p("outbound: %s", _clip(outbound_text, 4000))
        route_suffix = f" route={route}" if route else ""
        self._p(
            "END duration_ms=%d tool_calls=%d skills_used=%s%s",
            ms,
            self.tool_calls,
            ",".join(self.skills_used) or "(none)",
            route_suffix,
        )
        if usage:
            self._p(
                "usage: model=%s input=%s output=%s cache_read=%s cache_write=%s "
                "cache_hit_ratio=%.3f requests=%s",
                usage.get("model", "?"),
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                usage.get("cache_read_tokens", 0),
                usage.get("cache_write_tokens", 0),
                float(usage.get("cache_hit_ratio", 0.0) or 0.0),
                usage.get("requests", 0),
            )
