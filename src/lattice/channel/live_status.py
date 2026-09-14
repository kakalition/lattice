"""Live turn progress for channel UIs (edit status bubble / spinner).

Status text is deliberately persona-like: never tool names or internal
diagnostics. Phrases are drawn from large shuffled decks so the same line is
unlikely to appear twice within a month.
"""

from __future__ import annotations

import asyncio
import random
import re
import shlex
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from typing import Any, Protocol

from lattice.events import TurnEvents
from lattice.tool_names import normalize_name

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


def _basename(value: object) -> str:
    # Take the basename first, then clip, so a long path never truncates the name.
    text = str(value or "").strip()
    if not text:
        return ""
    name = text.rstrip("/").split("/")[-1] or text
    return _clip(name, 40)


def _host(value: object) -> str:
    text = str(value or "")
    return text.split("//", 1)[-1].split("/", 1)[0] if text else ""


def _outcome(result: str) -> str:
    head = (result or "").strip().lower()
    if head.startswith(("error:", "execute_script error:")):
        return "❌"
    if head.startswith("denied"):
        return "🚫"
    if re.match(r"exit=[1-9]\d*", head):
        return "⚠️"
    return "✅"


def _failure_note(result: str) -> str:
    """Plain-language reason a step did not succeed (details stay in the logs)."""
    head = (result or "").strip().lower()
    if head.startswith("denied"):
        return "you declined"
    if re.match(r"exit=[1-9]\d*", head):
        return "didn't finish"
    return "hit a problem"


# Shell verbs whose action is clear from the verb alone (fallback when we cannot
# name a target). Commands are never shown verbatim.
_SHELL_ACTIONS: dict[str, str] = {
    "pwd": "Checked the working directory",
    "git": "Checked the project history",
    "echo": "Checked a detail",
    "printf": "Checked a detail",
    "true": "Checked a detail",
}


def _tokens(part: str) -> list[str]:
    try:
        return shlex.split(part)
    except ValueError:
        return part.split()


def _flag_value(tokens: list[str], flag: str) -> str:
    for i, token in enumerate(tokens[:-1]):
        if token == flag:
            return _clip(tokens[i + 1].strip("'\""), 40)
    return ""


def _shell_friendly(command: str) -> tuple[str, str]:
    """(action, subject) for a shell command, derived from its first real verb."""
    segment = ""
    for part in re.split(r"&&|\|\||;|\|", command or ""):
        tokens = _tokens(part)
        if not tokens or tokens[0] in {"cd", "export", "set", "source"}:
            continue
        segment = part
        break
    if not segment:
        return "Checked a detail", ""
    tokens = _tokens(segment)
    verb = tokens[0].rsplit("/", 1)[-1]
    args = [t for t in tokens[1:] if not t.startswith("-")]
    first = _basename(args[0]) if args else ""

    if verb == "ls":
        return "Browsed", first or "the folder"
    if verb == "find":
        named = _flag_value(tokens, "-name") or _flag_value(tokens, "-iname")
        if named:
            return "Looked for", named
        return "Looked in", first or "your files"
    if verb in {"grep", "rg", "ag"}:
        return "Searched for", _clip(args[0].strip("'\""), 40) if args else "a phrase"
    if verb in {"cat", "head", "tail", "less", "more"}:
        return "Read", first or "a file"
    if verb in {"sed", "awk"}:
        files = [t for t in args if not re.fullmatch(r"[\d,;p\-]+", t)]
        return "Read", _basename(files[0]) if files else "a file"
    if verb == "mkdir":
        return "Made the folder", first
    if verb == "touch":
        return "Created", first or "a file"
    if verb in {"cp", "mv"}:
        return ("Copied" if verb == "cp" else "Moved"), _basename(args[-1]) if args else ""
    if verb == "rm":
        return "Tidied up", _basename(args[-1]) if args else ""
    if verb in {"python", "python3", "node"}:
        if "-" in tokens[1:] or "<<" in segment:
            return "Ran", "a script"
        return "Ran", first or "a script"
    if verb == "which":
        return "Checked whether", f"{args[0]} is installed" if args else ""
    if verb == "sqlite3":
        return "Checked the database", first
    if verb in _SHELL_ACTIONS:
        return _SHELL_ACTIONS[verb], ""
    return "Ran a command", ""


def _friendly_action(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """(action phrase, subject) in plain language; no tool names or raw commands."""
    name = normalize_name(name)
    get = args.get
    if name == "files/shell":
        return _shell_friendly(str(get("command") or ""))
    if name == "files/read":
        return "Read", _basename(get("path"))
    if name == "files/write":
        return "Wrote", _basename(get("path"))
    if name == "files/edit":
        return "Updated", _basename(get("path"))
    if name == "files/remove":
        return "Removed", _basename(get("path"))
    if name == "files/search":
        return "Looked for", _clip(get("pattern"), 40)
    if name == "web/search":
        return "Searched the web for", _clip(get("query"), 50)
    if name == "web/fetch":
        return "Opened", _host(get("url"))
    if name == "compute/script":
        target = get("path") or f"{get('language', '')} script"
        return "Ran", _basename(target)
    if name == "media/ocr":
        return "Read text from", _basename(get("path"))
    if name == "media/pdf":
        return "Made a PDF from", _basename(get("path"))
    if name == "media/chart":
        return "Made a chart from", _basename(get("path"))
    if name == "compute/calculator":
        return "Worked out", _clip(get("expression"), 40)
    if name == "interaction/clarify":
        return "Asked you a question", ""
    if name == "interaction/todo":
        return "Updated the to-do list", ""
    if name == "schedule/add":
        return "Set a reminder for", _clip(get("reminder"), 40)
    if name == "schedule/list":
        return "Checked your reminders", ""
    if name == "schedule/cancel":
        return "Cancelled a reminder", ""
    if name == "schedule/timezone_get":
        return "Checked the time zone", ""
    if name == "schedule/timezone_set":
        return "Set the time zone to", _clip(get("timezone"), 30)
    if name == "memory/session_search":
        return "Looked back through your chats for", _clip(get("query"), 40)
    if name == "memory/search":
        return "Recalled memories about", _clip(get("query"), 40)
    if name == "memory/add":
        return "Saved a memory", ""
    if name == "memory/update":
        return "Updated a memory", ""
    if name == "memory/forget":
        return "Forgot a memory", ""
    if name == "sqlite/list":
        return "Listed your databases", ""
    if name.startswith("sqlite/"):
        return "Worked on the database", _clip(get("name"), 30)
    if name == "skills/list":
        return "Listed the available skills", ""
    if name == "skills/view":
        return "Opened the skill", _clip(get("name"), 30)
    if name == "profiles/list":
        return "Listed your profiles", ""
    if name == "profiles/remove":
        return "Removed the profile", _clip(get("profile_id"), 30)
    if name == "browser/interact":
        return "Used the browser", ""
    if name == "browser/snapshot":
        return "Captured the page", ""
    return "Worked on your request", ""


def _describe_step(name: str, args: dict[str, Any], result: str, duration: float) -> dict[str, Any]:
    icon = _outcome(result)
    action, subject = _friendly_action(name, args or {})
    return {
        "icon": icon,
        "action": action,
        "subject": subject,
        "note": "" if icon == "✅" else _failure_note(result),
        "duration": duration,
    }


def _render_steps(steps: list[dict[str, Any]]) -> list[str]:
    """Collapse consecutive same-action steps into one readable line."""
    lines: list[str] = []
    i = 0
    while i < len(steps):
        step = steps[i]
        subjects = [step["subject"]] if step["subject"] else []
        total = step["duration"]
        j = i + 1
        while (
            j < len(steps)
            and not step["note"]
            and not steps[j]["note"]
            and steps[j]["icon"] == step["icon"]
            and steps[j]["action"] == step["action"]
        ):
            if steps[j]["subject"]:
                subjects.append(steps[j]["subject"])
            total += steps[j]["duration"]
            j += 1
        unique = list(dict.fromkeys(subjects))
        shown = ", ".join(unique[:3])
        if len(unique) > 3:
            shown += f" +{len(unique) - 3} more"
        line = f"- {step['icon']} {step['action']}"
        if shown:
            line += f" {shown}"
        if step["note"]:
            line += f" · {step['note']}"
        line += f" · {_format_duration(total)}"
        lines.append(line)
        i = j
    return lines


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
        self._steps: list[dict[str, Any]] = []
        self._tool_stack: list[tuple[str, float, dict[str, Any]]] = []
        self._stream_text = ""

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
        """Thinking line, finished steps, then the streaming reply so far."""
        elapsed = _format_duration(time.monotonic() - self._t0)
        head = f"{self._phrase_text} ({elapsed})"
        parts = [head]
        if self._steps:
            shown = self._steps[-_MAX_STEPS:]
            hidden = len(self._steps) - len(shown)
            body = "\n".join(_render_steps(shown))
            if hidden:
                plural = "s" if hidden != 1 else ""
                body = f"- … {hidden} earlier step{plural}\n{body}"
            parts.append(body)
        if self._stream_text:
            parts.append(self._stream_text)
        return "\n\n".join(parts)

    async def on_stream_delta(self, text: str) -> None:
        if self._closed or not text:
            return
        # Show the answer as it forms; the final reply still arrives as its own
        # message. Keeping only a tail avoids unbounded memory on long replies.
        self._stream_text = (self._stream_text + text)[-self.max_len :]
        await self._leave_idle()
        await self._emit(self._compose())

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
            with suppress(asyncio.CancelledError):
                await task
        pending = self._pending
        self._pending = None
        if pending and pending != self._last_text:
            with suppress(Exception):
                await self.sink.set_status(pending[: self.max_len])

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
            with suppress(asyncio.CancelledError):
                await task

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
        with suppress(Exception):
            await self.sink.set_status(text)


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
        with suppress(Exception):
            await send()
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
            return
        except TimeoutError:
            continue
