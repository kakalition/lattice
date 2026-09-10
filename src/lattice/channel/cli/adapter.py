"""CLI channel adapter — simple REPL (Textual TUI is separate)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from rich.console import Console
from rich.markdown import Markdown

from lattice.hitl.cli_adapter import CliHitlAdapter
from lattice.models import Inbound, Outbound
from lattice.session import SessionStore


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
            if line.startswith("/profile "):
                self.profile_id = line.split(maxsplit=1)[1].strip()
                self.session_id = None
                await self.store.set_sticky_profile("cli", "local", self.profile_id)
                self.console.print(f"switched profile → {self.profile_id} (new session)")
                continue
            if line == "/help":
                self.console.print("/profile <id>  /sessions  /resume <id>  /stop  /quit")
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
                inbound = Inbound(
                    text=line,
                    profile_id=self.profile_id,
                    user_id="local",
                    channel="cli",
                    session_id=self.session_id,
                    steer_text=self._steer,
                )
                self._steer = None
                outbound = await handler(inbound)
                await self.send(outbound)
                while not self._queue.empty():
                    nxt = await self._queue.get()
                    outbound = await handler(
                        Inbound(
                            text=nxt,
                            profile_id=self.profile_id,
                            user_id="local",
                            channel="cli",
                            session_id=self.session_id,
                        )
                    )
                    await self.send(outbound)
            finally:
                self._busy = False
