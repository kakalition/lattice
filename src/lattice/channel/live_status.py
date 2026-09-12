"""Live turn progress for channel UIs (edit status bubble / spinner).

Status text is deliberately persona-like: never tool names or internal
diagnostics. Phrases are drawn from large shuffled decks so the same line is
unlikely to appear twice within a month.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Protocol

from lattice.events import TurnEvents

_bound: ContextVar[TurnEvents | None] = ContextVar("lattice_live_events", default=None)


class PhraseDeck:
    """Endless, non-repeating shuffled sequence over actions × objects × emoji."""

    def __init__(
        self,
        actions: Sequence[str],
        objects: Sequence[str],
        emojis: Sequence[str],
        *,
        template: str = "{emoji} {action} {object}…",
        seed: int | None = None,
    ) -> None:
        self._actions = tuple(actions)
        self._objects = tuple(objects)
        self._emojis = tuple(emojis)
        self._template = template
        self._count = len(self._actions) * len(self._objects) * len(self._emojis)
        self._rng = random.Random(seed)
        self._deck: list[int] = []

    @property
    def size(self) -> int:
        return self._count

    def next(self) -> str:
        if self._count == 0:
            return "thinking…"
        if not self._deck:
            self._deck = list(range(self._count))
            self._rng.shuffle(self._deck)
        idx = self._deck.pop()
        per_action = len(self._objects)
        per_emoji = len(self._actions) * per_action
        emoji = idx // per_emoji
        rest = idx % per_emoji
        action = rest // per_action
        obj = rest % per_action
        return self._template.format(
            emoji=self._emojis[emoji], action=self._actions[action], object=self._objects[obj]
        )


_THINK_ACTIONS = (
    "mulling over",
    "scrolling through",
    "turning over",
    "sifting through",
    "noodling on",
    "poking at",
    "sketching out",
    "untangling",
    "weighing up",
    "lining up",
    "chewing on",
    "rummaging through",
    "piecing together",
    "double-checking",
    "tracing",
    "simmering on",
    "gathering",
    "polishing",
    "re-reading",
    "squinting at",
    "tidying up",
    "revisiting",
    "threading together",
    "mapping out",
    "puzzling over",
    "smoothing out",
    "shuffling",
    "plucking at",
    "brewing",
    "unpacking",
)
_THINK_OBJECTS = (
    "the details",
    "that thought",
    "the moving parts",
    "the fine print",
    "the pieces",
    "the options",
    "the angles",
    "the threads",
    "the possibilities",
    "the wording",
    "the next step",
    "the edges",
    "the plan",
    "the numbers",
    "the loose ends",
    "the shape of it",
    "the trade-offs",
    "the timing",
    "the bigger picture",
    "the small stuff",
    "the breadcrumbs",
    "the why",
    "the how",
    "the whole thing",
    "a few ideas",
    "the quiet parts",
    "the half-formed bits",
    "the shape of the answer",
)
_THINK_EMOJI = (
    "🧠",
    "✨",
    "🤔",
    "🌀",
    "📝",
    "🔍",
    "🧩",
    "💭",
    "🌿",
    "⚙️",
    "🪄",
    "📚",
    "🗺️",
    "🕰️",
    "🫧",
    "🧵",
    "🛠️",
    "🔮",
    "🌟",
    "☕",
    "🍃",
    "🧭",
    "🪶",
    "🎨",
    "🧮",
    "📎",
    "🌤️",
    "🪴",
    "🫖",
    "🧊",
    "🕯️",
    "🎧",
    "💡",
    "🔖",
    "🧷",
    "📐",
    "🧪",
    "🌈",
    "🫐",
    "🪄",
    "🌸",
    "🧸",
    "🎐",
    "🪁",
)

_APPROVAL_PHRASES = (
    "just needs your go-ahead 🙏",
    "waiting on your nod 👀",
    "your call here ✨",
    "ready when you are 🌿",
    "just your blessing needed 🙌",
    "holding for your approval 🫶",
    "one tiny yes away ✅",
    "at your discretion 🎛️",
    "awaiting your verdict ⚖️",
    "your move 🎲",
    "just checking with you first 💬",
    "standing by for your say-so 🛎️",
)
_RESUME_PHRASES = (
    "back to it 🚀",
    "carrying on ✨",
    "picking the thread back up 🧵",
    "right, onward 🌿",
    "continuing where we left off 🪄",
    "and we're moving again 🌊",
    "resuming 🔄",
    "back in the flow 🎐",
)

_CONFIRM_LEADS = (
    "All set",
    "Done",
    "Sorted",
    "Handled",
    "Consider it done",
    "Taken care of",
    "Wrapped up",
    "Good to go",
    "Locked in",
    "Nailed it",
    "Tucked away",
    "Finished up",
    "All clear",
    "Job done",
    "That's handled",
    "Done and dusted",
    "Squared away",
    "Off the list",
)
_CONFIRM_TAILS = (
    "anything else?",
    "nice and tidy.",
    "just as you asked.",
    "smoothly.",
    "with care.",
    "no loose ends.",
    "ready when you are.",
    "right on cue.",
    "and looking good.",
    "quietly handled.",
    "exactly as planned.",
    "one less thing to think about.",
)
_CONFIRM_EMOJI = (
    "✨",
    "🌿",
    "💪",
    "✅",
    "🧺",
    "🗂️",
    "🌟",
    "🤍",
    "🎁",
    "🚀",
    "🔒",
    "💫",
    "🙌",
    "🫶",
    "🍃",
    "🎐",
    "🪄",
    "🌸",
    "☕",
    "🧸",
    "🌈",
    "🕊️",
    "🪴",
    "🫧",
)

_THINK_DECK = PhraseDeck(_THINK_ACTIONS, _THINK_OBJECTS, _THINK_EMOJI)
_CONFIRM_DECK = PhraseDeck(
    _CONFIRM_LEADS,
    _CONFIRM_TAILS,
    _CONFIRM_EMOJI,
    template="{emoji} {action} — {object}",
)

# A bare acknowledgement gets a warm, varied confirmation instead.
_TERSE_ACKS = frozenset(
    {
        "ok",
        "okay",
        "k",
        "kk",
        "sure",
        "done",
        "got it",
        "gotcha",
        "alright",
        "all right",
        "will do",
        "on it",
        "sounds good",
        "cool",
        "nice",
        "great",
        "yep",
        "yes",
        "yup",
        "no problem",
        "np",
        "sure thing",
        "noted",
        "understood",
        "roger",
    }
)


class StatusSink(Protocol):
    async def set_status(self, text: str) -> None: ...


def _format_duration(seconds: float) -> str:
    return f"{seconds:.1f}s" if seconds < 9.95 else f"{seconds:.0f}s"


# Most-recent finished steps kept in the status bubble; older ones are elided.
_MAX_STEPS = 20


def _clip(value: object, limit: int = 60) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _code(value: object, limit: int = 60) -> str:
    clipped = _clip(value, limit)
    return f"`{clipped}`" if clipped else ""


def _quoted(value: object, limit: int = 50) -> str:
    clipped = _clip(value, limit)
    return f"“{clipped}”" if clipped else ""


def _outcome(result: str) -> str:
    head = (result or "").strip().lower()
    if head.startswith(("error:", "execute_script error:")):
        return "❌"
    if head.startswith("denied"):
        return "🚫"
    if re.match(r"exit=[1-9]\d*", head):
        return "⚠️"
    return "✅"


def _result_note(result: str) -> str:
    """A short success/failure signal from the tool result (never full output)."""
    head = (result or "").strip()
    if not head:
        return ""
    match = re.match(r"exit=(\d+)", head)
    if match:
        return f"exit {match.group(1)}"
    if head.lower().startswith(("error:", "denied")):
        return _clip(head.splitlines()[0], 60)
    return ""


# tool name → (action verb, argument hint). Hints clip to keep the bubble compact
# and deliberately exclude bulk content (file bodies, script code).
_STEP_DESCRIBERS: dict[str, tuple[str, Callable[[dict[str, Any]], str]]] = {
    "shell": ("ran", lambda a: _code(a.get("command"))),
    "execute_script": (
        "ran a script",
        lambda a: _code(a.get("path") or f"{a.get('language', '')} inline"),
    ),
    "read_file": ("read", lambda a: _code(a.get("path"))),
    "write_file": ("wrote", lambda a: _code(a.get("path"))),
    "edit_file": ("edited", lambda a: _code(a.get("path"))),
    "remove_path": ("removed", lambda a: _code(a.get("path"))),
    "search_files": ("searched files for", lambda a: _code(a.get("pattern"))),
    "ocr": ("extracted text from", lambda a: _code(a.get("path"))),
    "generate_pdf": ("made a PDF at", lambda a: _code(a.get("path"))),
    "generate_chart": ("made a chart at", lambda a: _code(a.get("path"))),
    "web_search": ("searched the web for", lambda a: _quoted(a.get("query"))),
    "web_fetch": ("fetched", lambda a: _code(a.get("url"))),
    "browser_interact": ("browsed", lambda a: _code(a.get("action"))),
    "browser_snapshot": ("captured the page", lambda a: ""),
    "clarify": ("asked you", lambda a: _quoted(a.get("question"))),
    "calculator": ("calculated", lambda a: _code(a.get("expression"))),
    "todo": ("updated the todo list", lambda a: _code(a.get("action"))),
    "schedule_add": ("scheduled", lambda a: _quoted(a.get("reminder"))),
    "schedule_list": ("listed reminders", lambda a: ""),
    "schedule_cancel": ("cancelled a reminder", lambda a: _code(a.get("job_id"))),
    "timezone_get": ("checked the timezone", lambda a: ""),
    "timezone_set": ("set the timezone to", lambda a: _code(a.get("timezone"))),
    "session_search": ("searched past sessions for", lambda a: _quoted(a.get("query"))),
    "memory_search": ("recalled memories for", lambda a: _quoted(a.get("query"))),
    "memory_add": ("saved a memory", lambda a: _quoted(a.get("text"), 40)),
    "memory_update": ("updated a memory", lambda a: _code(a.get("memory_id"))),
    "memory_forget": ("forgot a memory", lambda a: _code(a.get("memory_id"))),
    "sqlite_list": ("listed databases", lambda a: ""),
    "sqlite_schema": ("inspected the schema of", lambda a: _code(a.get("name"))),
    "sqlite_query": ("queried", lambda a: _code(a.get("name"))),
    "sqlite_execute": ("wrote to", lambda a: _code(a.get("name"))),
    "sqlite_register": ("registered", lambda a: _code(a.get("name"))),
    "sqlite_unregister": ("unregistered", lambda a: _code(a.get("name"))),
    "sqlite_backup": ("backed up", lambda a: _code(a.get("name"))),
    "skills_list": ("listed skills", lambda a: ""),
    "skill_view": ("loaded the skill", lambda a: _code(a.get("name"))),
    "profile_list": ("listed profiles", lambda a: ""),
    "profile_remove": ("removed the profile", lambda a: _code(a.get("profile_id"))),
}


def _describe_step(name: str, args: dict[str, Any], result: str, duration: float) -> str:
    """One Markdown bullet: outcome, tool, action + arg hint, result note, duration."""
    verb, hint_of = _STEP_DESCRIBERS.get(name, ("ran", lambda a: ""))
    try:
        hint = hint_of(args or {})
    except Exception:
        hint = ""
    detail = f"{verb} {hint}".strip()
    parts = [f"- {_outcome(result)} **{name}**"]
    if detail:
        parts.append(detail)
    note = _result_note(result)
    if note:
        parts.append(note)
    parts.append(_format_duration(duration))
    return " · ".join(parts)


def current_live_events() -> TurnEvents | None:
    return _bound.get()


def thinking_phrase() -> str:
    return _THINK_DECK.next()


def idle_phrase(index: int = 0, phrases: Sequence[str] | None = None) -> str:
    """A creative thinking line. Explicit ``phrases`` are indexed modulo; the
    default pool is a large shuffled deck."""
    if phrases is not None:
        pool = tuple(phrases)
        if pool:
            return pool[index % len(pool)]
    return thinking_phrase()


def warm_confirmation(text: str) -> str | None:
    """Return a warm replacement if ``text`` is a bare acknowledgement, else None."""
    raw = (text or "").strip()
    if not raw or len(raw) > 24:
        return None
    normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", raw.lower())).strip()
    if normalized in _TERSE_ACKS:
        return _CONFIRM_DECK.next()
    # A lone emoji acknowledgement (e.g. "👍") also deserves something warmer.
    if not normalized and any(ord(ch) > 0x2190 for ch in raw):
        return _CONFIRM_DECK.next()
    return None


def format_status_message(message: str) -> str:
    """Map an internal status line to a persona-like phrase (no tool details)."""
    low = (message or "").strip().lower()
    if low.startswith("hitl_ask"):
        return _pick(_APPROVAL_PHRASES)
    if low.startswith("hitl_decision"):
        return _pick(_RESUME_PHRASES)
    return thinking_phrase()


def _pick(pool: Sequence[str]) -> str:
    return pool[random.randrange(len(pool))]


class LiveTurnEvents:
    """Throttle status updates so Telegram/CLI stay responsive without flood."""

    def __init__(
        self,
        sink: StatusSink,
        *,
        min_interval_s: float = 0.45,
        phrase_interval_s: float = 2.4,
        max_len: int = 3500,
        phrases: Sequence[str] | None = None,
    ) -> None:
        self.sink = sink
        self.min_interval_s = min_interval_s
        self.phrase_interval_s = phrase_interval_s
        self.max_len = max_len
        self.phrases = phrases
        self._last_sent = 0.0
        self._last_text = ""
        self._pending: str | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._idle = False
        self._phrase_idx = 0
        self._lock = asyncio.Lock()
        self._closed = False
        # Turn progress: elapsed time anchors the thinking line, finished tool
        # steps accumulate beneath it.
        self._t0 = time.monotonic()
        self._phrase_text = ""
        self._steps: list[str] = []
        self._tool_stack: list[tuple[str, float, dict[str, Any]]] = []

    async def on_status(self, message: str) -> None:
        low = (message or "").strip().lower()
        if low.startswith("hitl_ask"):
            await self._leave_idle()
            self._phrase_text = _pick(_APPROVAL_PHRASES)
            await self._emit(self._compose())
            return
        # Any other status keeps the warm, rotating thinking line going.
        await self._enter_idle()

    def _phrase(self) -> str:
        text = idle_phrase(self._phrase_idx, self.phrases)
        self._phrase_idx += 1
        return text

    def _compose(self) -> str:
        """Thinking line with elapsed time, then finished steps as Markdown bullets."""
        elapsed = _format_duration(time.monotonic() - self._t0)
        head = f"{self._phrase_text} ({elapsed})"
        if not self._steps:
            return head
        shown = self._steps[-_MAX_STEPS:]
        hidden = len(self._steps) - len(shown)
        body = "\n".join(shown)
        if hidden:
            plural = "s" if hidden != 1 else ""
            body = f"- … {hidden} earlier step{plural}\n{body}"
        return f"{head}\n\n{body}"

    async def on_stream_delta(self, text: str) -> None:
        return None

    async def on_tool_start(self, name: str, args: dict[str, Any]) -> None:
        # Arguments steer the finished-step summary; raw result bodies stay hidden.
        self._tool_stack.append((name, time.monotonic(), args))
        await self._enter_idle()

    async def on_tool_end(self, name: str, result: str) -> None:
        duration = 0.0
        args: dict[str, Any] = {}
        for i in range(len(self._tool_stack) - 1, -1, -1):
            if self._tool_stack[i][0] == name:
                _, started, args = self._tool_stack.pop(i)
                duration = time.monotonic() - started
                break
        self._steps.append(_describe_step(name, args, result, duration))
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
        self._phrase_text = self._phrase()
        self._idle = True
        await self._emit(self._compose())
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
                self._phrase_text = self._phrase()
                await self._emit(self._compose())
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
