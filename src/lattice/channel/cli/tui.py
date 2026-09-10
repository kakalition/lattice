"""Textual rich TUI for Lattice chat + HITL modal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

from lattice.hitl.base import ApprovalDecision, ApprovalRequest, ClarifyRequest
from lattice.models import Inbound, Outbound


class HitlModal(ModalScreen[str]):
    def __init__(self, title: str, body: str, choices: list[str] | None = None) -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._choices = choices or ["Approve", "Deny"]

    def compose(self) -> ComposeResult:
        with Vertical(id="hitl-modal"):
            yield Label(self._title, id="hitl-title")
            yield Static(self._body, id="hitl-body")
            with Horizontal():
                for choice in self._choices:
                    yield Button(choice, id=f"btn-{choice}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        label = event.button.label
        self.dismiss(str(label))


class TuiHitl:
    def __init__(self, app: LatticeTui) -> None:
        self.app = app
        self.timeout_seconds = 600

    async def approve(self, req: ApprovalRequest) -> ApprovalDecision:
        choice = await self.app.push_screen_wait(
            HitlModal("HITL Approve", f"{req.tool_name}: {req.summary}")
        )
        return ApprovalDecision.APPROVE if choice == "Approve" else ApprovalDecision.DENY

    async def clarify(self, req: ClarifyRequest) -> str:
        choices = req.choices or ["OK"]
        choice = await self.app.push_screen_wait(HitlModal("Clarify", req.question, choices))
        return choice or ""


class LatticeTui(App[None]):
    CSS = """
    #log { height: 1fr; }
    #status { dock: bottom; height: 1; }
    #input { dock: bottom; margin-bottom: 1; }
    #hitl-modal {
      padding: 1 2; width: 60; height: auto;
      border: thick $accent; background: $surface;
    }
    """

    BINDINGS = [("q", "quit", "Quit"), ("ctrl+c", "quit", "Quit")]

    def __init__(
        self,
        handler: Callable[[Inbound], Awaitable[Outbound]],
        *,
        profile_id: str = "default",
    ) -> None:
        super().__init__()
        self.handler = handler
        self.profile_id = profile_id
        self.session_id: str | None = None
        self.hitl = TuiHitl(self)
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", markup=True)
        yield Static(f"profile={self.profile_id}", id="status")
        yield Input(placeholder="Message Lattice… (/profile, /quit)", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#input", Input).focus()
        log = self.query_one("#log", RichLog)
        log.write(f"[bold]Lattice TUI[/] profile={self.profile_id}")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        log = self.query_one("#log", RichLog)
        status = self.query_one("#status", Static)
        if text in {"/quit", "/q"}:
            self.exit()
            return
        if text.startswith("/profile remove ") or text.startswith("/profile rm "):
            pid = text.split(maxsplit=2)[2].strip()
            try:
                from lattice.profiles import remove_profile

                remove_profile(pid)
                if self.profile_id == pid:
                    self.profile_id = "default"
                    self.session_id = None
                status.update(f"profile={self.profile_id}")
                log.write(f"[cyan]removed profile → {pid}[/]")
            except (ValueError, FileNotFoundError) as exc:
                log.write(f"[red]{exc}[/]")
            return
        if text.startswith("/profile "):
            self.profile_id = text.split(maxsplit=1)[1].strip()
            self.session_id = None
            status.update(f"profile={self.profile_id}")
            log.write(f"[cyan]switched profile → {self.profile_id}[/]")
            return
        if self._busy:
            log.write("[yellow]busy — message ignored (use queue in REPL adapter)[/]")
            return
        self._busy = True
        log.write(f"[green]you:[/] {text}")
        status.update(f"profile={self.profile_id} | thinking…")
        try:
            outbound = await self.handler(
                Inbound(
                    text=text,
                    profile_id=self.profile_id,
                    channel="cli",
                    session_id=self.session_id,
                )
            )
            self.session_id = outbound.session_id
            log.write(f"[magenta]lattice:[/] {outbound.text}")
        except Exception as exc:
            log.write(f"[red]error:[/] {exc}")
        finally:
            self._busy = False
            status.update(f"profile={self.profile_id}")


async def run_tui(
    handler: Callable[[Inbound], Awaitable[Outbound]], *, profile_id: str = "default"
) -> None:
    app = LatticeTui(handler, profile_id=profile_id)
    await app.run_async()
