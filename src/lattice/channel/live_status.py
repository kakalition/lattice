"""Live turn progress for channel UIs (edit status bubble / spinner)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Protocol

from lattice.events import TurnEvents

_bound: ContextVar[TurnEvents | None] = ContextVar("lattice_live_events", default=None)

# Shown while the model is working between concrete tool/status updates.
IDLE_PHRASES: tuple[str, ...] = (
    "mulling it over…",
    "weighing options…",
    "connecting dots…",
    "chewing on that…",
    "sorting thoughts…",
    "lining things up…",
    "holding the thread…",
    "figuring next step…",
    "rereading the ask…",
    "narrowing it down…",
    "almost there…",
    "stitching an answer…",
)


class StatusSink(Protocol):
    async def set_status(self, text: str) -> None: ...


def current_live_events() -> TurnEvents | None:
    return _bound.get()


def idle_phrase(index: int = 0, phrases: Sequence[str] | None = None) -> str:
    pool = phrases or IDLE_PHRASES
    return pool[index % len(pool)]


def format_tool_activity(name: str, args: dict[str, Any]) -> str:
    """Short human-readable line for the active tool."""
    if name == "shell":
        cmd = str(args.get("command") or args.get("cmd") or "").strip().replace("\n", " ")
        if cmd:
            return f"shell: {_clip(cmd, 72)}…"
        return "shell…"
    if name in {"read_file", "write_file", "edit_file", "search_files", "ocr"}:
        path = str(args.get("path") or args.get("query") or "").strip()
        if path:
            return f"{name}: {_clip(path, 64)}…"
        return f"{name}…"
    if name in {"generate_pdf", "generate_chart"}:
        path = str(args.get("path") or "").strip()
        title = str(args.get("title") or "").strip()
        tip = title or path
        if tip:
            return f"{name}: {_clip(tip, 64)}…"
        return f"{name}…"
    if name.startswith("sqlite_"):
        db = str(args.get("database") or args.get("name") or "").strip()
        if db:
            return f"{name} ({db})…"
        return f"{name}…"
    if name in {"web_search", "web_fetch"}:
        q = str(args.get("query") or args.get("url") or "").strip()
        if q:
            return f"{name}: {_clip(q, 64)}…"
        return f"{name}…"
    if name == "skill_view":
        skill = str(args.get("name") or args.get("skill") or "").strip()
        if skill:
            return f"skill: {skill}…"
        return "skill…"
    if name == "delegate":
        return "delegate…"
    if name.startswith("memory_"):
        return f"{name}…"
    if name.startswith("schedule_"):
        return f"{name}…"
    return f"{name}…"


def format_status_message(message: str) -> str:
    msg = (message or "").strip()
    if not msg or _is_idle_status(msg):
        return idle_phrase(0)
    low = msg.lower()
    if low.startswith("hitl_ask"):
        return "waiting for approval…"
    if low.startswith("hitl_decision"):
        return "continuing…"
    if msg.endswith("…") or msg.endswith("..."):
        return msg
    return f"{msg}…"


def _is_idle_status(message: str) -> bool:
    low = message.strip().lower().rstrip(".…")
    return low in {"thinking", "idle", "working"}


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class LiveTurnEvents:
    """Throttle status updates so Telegram/CLI stay responsive without flood."""

    def __init__(
        self,
        sink: StatusSink,
        *,
        min_interval_s: float = 0.45,
        phrase_interval_s: float = 2.4,
        max_len: int = 200,
        phrases: Sequence[str] | None = None,
    ) -> None:
        self.sink = sink
        self.min_interval_s = min_interval_s
        self.phrase_interval_s = phrase_interval_s
        self.max_len = max_len
        self.phrases: Sequence[str] = phrases or IDLE_PHRASES
        self._last_sent = 0.0
        self._last_text = ""
        self._pending: str | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._idle = False
        self._phrase_idx = 0
        self._lock = asyncio.Lock()
        self._closed = False

    async def on_status(self, message: str) -> None:
        if _is_idle_status(message or ""):
            await self._enter_idle()
            return
        await self._leave_idle()
        await self._emit(format_status_message(message))

    async def on_stream_delta(self, text: str) -> None:
        return None

    async def on_tool_start(self, name: str, args: dict[str, Any]) -> None:
        await self._leave_idle()
        await self._emit(format_tool_activity(name, args))

    async def on_tool_end(self, name: str, result: str) -> None:
        await self._enter_idle()

    async def close(self) -> None:
        self._closed = True
        await self._leave_idle()
        task = self._flush_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        pending = self._pending
        self._pending = None
        if pending and pending != self._last_text:
            try:
                await self.sink.set_status(pending[: self.max_len])
            except Exception:
                pass

    async def _enter_idle(self) -> None:
        if self._closed:
            return
        phrase = idle_phrase(self._phrase_idx, self.phrases)
        self._phrase_idx += 1
        self._idle = True
        await self._emit(phrase)
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._rotate_idle())

    async def _leave_idle(self) -> None:
        self._idle = False
        task = self._idle_task
        self._idle_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _rotate_idle(self) -> None:
        try:
            while not self._closed and self._idle:
                await asyncio.sleep(self.phrase_interval_s)
                if self._closed or not self._idle:
                    return
                phrase = idle_phrase(self._phrase_idx, self.phrases)
                self._phrase_idx += 1
                await self._emit(phrase)
        except asyncio.CancelledError:
            return

    async def _emit(self, text: str) -> None:
        if self._closed:
            return
        text = text[: self.max_len]
        async with self._lock:
            if text == self._last_text and self._pending is None:
                return
            now = time.monotonic()
            wait = self.min_interval_s - (now - self._last_sent)
            if wait <= 0:
                self._pending = None
                self._last_sent = now
                self._last_text = text
                await self.sink.set_status(text)
                return
            self._pending = text
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._flush_later(wait))

    async def _flush_later(self, wait: float) -> None:
        try:
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self._closed or self._pending is None:
                return
            text = self._pending
            self._pending = None
            if text == self._last_text:
                return
            self._last_sent = time.monotonic()
            self._last_text = text
        try:
            await self.sink.set_status(text)
        except Exception:
            pass


@asynccontextmanager
async def bind_live_events(events: LiveTurnEvents) -> AsyncIterator[LiveTurnEvents]:
    token = _bound.set(events)
    try:
        yield events
    finally:
        await events.close()
        _bound.reset(token)


async def typing_keepalive(
    send: Callable[[], Awaitable[None]],
    *,
    interval_s: float = 4.0,
    stop: asyncio.Event | None = None,
) -> None:
    """Refresh chat 'typing' indicator while a turn runs."""
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            await send()
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
            return
        except TimeoutError:
            continue
