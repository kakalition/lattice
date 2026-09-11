"""CLI channel adapter — simple REPL (Textual TUI is separate)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from rich.console import Console
from rich.markdown import Markdown

from lattice.channel.live_status import LiveTurnEvents, bind_live_events, idle_phrase
from lattice.hitl.cli_adapter import CliHitlAdapter
from lattice.models import Inbound, Outbound
from lattice.session import SessionStore

logger = logging.getLogger("lattice.channel.cli")


class _RichStatusSink:
    def __init__(self, console: Console) -> None:
        self._status = console.status(idle_phrase(0), spinner="dots")
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._status.start()
            self._started = True

    def stop(self) -> None:
        if self._started:
            self._status.stop()
            self._started = False

    async def set_status(self, text: str) -> None:
        self._status.update(text)


class CliAdapter:
    def __init__(
        self,
        *,
        profile_id: str = "default",
        console: Console | None = None,
        hitl: CliHitlAdapter | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.console = console or Console()
        self.hitl = hitl or CliHitlAdapter(console=self.console)
        self.store = store or SessionStore()
        self.session_id: str | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._busy = False
        self._steer: str | None = None

    def name(self) -> str:
        return "cli"

    async def send(self, msg: Outbound) -> None:
        self.console.print(Markdown(msg.text))
        if msg.session_id:
            self.session_id = msg.session_id

    async def _handle(self, handler: Callable[[Inbound], Awaitable[Outbound]], text: str) -> None:
        sink = _RichStatusSink(self.console)
        sink.start()
        live = LiveTurnEvents(sink, min_interval_s=0.2)
        try:
            async with bind_live_events(live):
                await live.on_status("thinking")
                inbound = Inbound(
                    text=text,
                    profile_id=self.profile_id,
                    user_id="local",
                    channel="cli",
                    session_id=self.session_id,
                    steer_text=self._steer,
                )
                self._steer = None
                logger.info("recv profile=%s text=%s", self.profile_id, text[:200])
                outbound = await handler(inbound)
                logger.info("send chars=%d", len(outbound.text or ""))
                await self.send(outbound)
        finally:
            sink.stop()

    async def run(self, handler: Callable[[Inbound], Awaitable[Outbound]]) -> None:
        self.console.print(f"[bold]Lattice[/] profile=[cyan]{self.profile_id}[/] — /help, /quit")
        while True:
            try:
                line = await asyncio.to_thread(lambda: self.console.input("[bold green]>[/] "))
            except (EOFError, KeyboardInterrupt):
                self.console.print("bye")
                break
            line = line.strip()
            if not line:
                continue
            if line in {"/quit", "/exit", "/q"}:
                break
            if line.startswith("/profile remove ") or line.startswith("/profile rm "):
                pid = line.split(maxsplit=2)[2].strip()
                try:
                    from lattice.profiles import remove_profile

                    remove_profile(pid)
                    await self.store.clear_sticky_for_profile(pid)
                    if self.profile_id == pid:
                        self.profile_id = "default"
                        self.session_id = None
                        await self.store.set_sticky_profile("cli", "local", "default")
                    self.console.print(f"removed profile → {pid}")
                except (ValueError, FileNotFoundError) as exc:
                    self.console.print(f"[red]{exc}[/]")
                continue
            if line.startswith("/profile "):
                self.profile_id = line.split(maxsplit=1)[1].strip()
                self.session_id = None
                await self.store.set_sticky_profile("cli", "local", self.profile_id)
                self.console.print(f"switched profile → {self.profile_id} (new session)")
                continue
            if line == "/model" or line.startswith("/model "):
                from lattice.config import load_settings
                from lattice.profiles import get_profile
                from lattice.providers.settings import normalize_primary_model_id, resolve_model_id

                settings = load_settings()
                rest = line[len("/model") :].strip()
                if rest.lower() == "clear":
                    await self.store.clear_sticky_primary_model("cli", "local")
                    self.console.print("primary model sticky cleared")
                    continue
                if rest:
                    try:
                        model_id = normalize_primary_model_id(rest)
                    except ValueError as exc:
                        self.console.print(f"[red]{exc}[/]")
                        continue
                    await self.store.set_sticky_primary_model("cli", "local", model_id)
                    self.console.print(f"primary model → {model_id}")
                    continue
                sticky = await self.store.get_sticky_primary_model("cli", "local")
                profile_model = None
                try:
                    profile = get_profile(self.profile_id, settings.home)
                    profile_model = profile.primary_model or profile.model
                except Exception:
                    pass
                effective = resolve_model_id(
                    settings, profile_model=profile_model, sticky_model=sticky
                )
                if sticky:
                    self.console.print(f"{effective} (sticky)")
                elif profile_model:
                    self.console.print(f"{effective} (profile {self.profile_id})")
                else:
                    self.console.print(f"{effective} (config)")
                continue
            if line == "/help":
                self.console.print(
                    "/profile <id>  /profile remove <id>  /model [id|clear]  "
                    "/sessions  /resume <id>  /stop  /quit"
                )
                continue
            if line == "/sessions":
                rows = await self.store.list_sessions(profile_id=self.profile_id, limit=15)
                for r in rows:
                    self.console.print(f"{r['id'][:8]}… {r['updated_at']} {r.get('title') or ''}")
                continue
            if line.startswith("/resume "):
                self.session_id = line.split(maxsplit=1)[1].strip()
                self.console.print(f"resumed {self.session_id}")
                continue
            if line == "/stop":
                self.console.print("stop noted (no active turn cancel token in REPL)")
                continue

            if self._busy:
                # steer into next tool boundary conceptually — queue bounded
                if self._queue.qsize() >= 8:
                    self.console.print("[yellow]queue full[/]")
                    continue
                await self._queue.put(line)
                self.console.print("[dim]queued / steer[/]")
                continue

            self._busy = True
            try:
                await self._handle(handler, line)
                while not self._queue.empty():
                    nxt = await self._queue.get()
                    await self._handle(handler, nxt)
            finally:
                self._busy = False
