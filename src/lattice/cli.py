"""Typer CLI entrypoint."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer
from rich.console import Console

from lattice import __version__
from lattice.branding import brand_label
from lattice.config import load_settings
from lattice.hitl import AutoApproveHitl, CliHitlAdapter
from lattice.logging_config import setup_logging
from lattice.providers.settings import resolve_api_key
from lattice.setup import doctor_report, init_home
from lattice.turn import echo_turn, run_turn

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Lattice personal agent")
console = Console()
logger = logging.getLogger("lattice.cli")


def _run(coro):
    return asyncio.run(coro)


@app.callback()
def main() -> None:
    """Lattice — thin-waist personal agent."""


@app.command()
def version() -> None:
    """Show version."""
    console.print(__version__)


@app.command("init")
def init_cmd(
    home: Path | None = typer.Option(None, help="Override Lattice home (default <project>/.lattice)"),
) -> None:
    """Create <project>/.lattice layout, default profile, and skill starters."""
    root = init_home(home)
    setup_logging()
    console.print(f"initialized {root}")


@app.command()
def doctor(
    home: Path | None = typer.Option(None, help="Override Lattice home"),
) -> None:
    """Diagnose config / keys / profiles."""
    settings = load_settings(home)
    setup_logging()
    for line in doctor_report(home):
        console.print(line)


@app.command()
def chat(
    profile: str = typer.Option("default", "--profile", "-p", help="Profile id"),
    tui: bool = typer.Option(False, "--tui", help="Use Textual TUI"),
    workspace: Path | None = typer.Option(None, "-w", help="Workspace root"),
    echo: bool = typer.Option(False, "--echo", help="Offline echo (no LLM)"),
) -> None:
    """Interactive CLI chat."""
    settings = load_settings()
    if workspace:
        settings.agent.workspace = workspace
    init_home(settings.home)
    setup_logging()

    async def handler(inbound):
        inbound.profile_id = inbound.profile_id or profile
        if echo or not resolve_api_key(settings):
            if not echo and not resolve_api_key(settings):
                console.print("[yellow]No API key — using echo mode. Set OPENAI_API_KEY.[/]")
            return await echo_turn(inbound)
        hitl = CliHitlAdapter(timeout_seconds=settings.agent.hitl_timeout_seconds)
        return await run_turn(inbound, settings=settings, hitl=hitl)

    if tui:
        from lattice.channel.cli.tui import run_tui

        _run(run_tui(handler, profile_id=profile))
        return

    from lattice.channel.cli.adapter import CliAdapter

    adapter = CliAdapter(profile_id=profile)
    _run(adapter.run(handler))


@app.command()
def gateway(
    once: bool = typer.Option(False, "--once", help="Run scheduler once then exit (no telegram)"),
) -> None:
    """Run Telegram + scheduler gateway (pidfile locked)."""
    from lattice.channel.telegram.adapter import TelegramAdapter
    from lattice.channel.telegram.deliver import deliver_telegram
    from lattice.gateway import PidfileLock
    from lattice.hitl import TelegramHitlAdapter
    from lattice.models import Outbound
    from lattice.scheduler import SchedulerRunner
    from lattice.session import SessionStore

    settings = load_settings()
    init_home(settings.home)
    from lattice.timeutil import ensure_timezone

    settings.timezone = ensure_timezone(settings.home)
    log_path = setup_logging()
    lock = PidfileLock(settings.home / "gateway.pid")
    lock.acquire()
    store = SessionStore(settings.home / "state.db")
    hitl = TelegramHitlAdapter(timeout_seconds=settings.agent.hitl_timeout_seconds)
    runner = SchedulerRunner(home=settings.home)

    async def handler(inbound):
        if not resolve_api_key(settings):
            return await echo_turn(inbound)
        local_hitl = hitl if inbound.channel == "telegram" else AutoApproveHitl(approve_all=False)
        return await run_turn(inbound, settings=settings, hitl=local_hitl, session_store=store)

    async def send_fn(channel: str, outbound: Outbound) -> None:
        if channel == "telegram":
            await deliver_telegram(settings, outbound)
        elif channel == "cli":
            console.print(f"[cyan]scheduler[/] {outbound.text}")
        else:
            logger.warning("unknown deliver channel %s", channel)

    async def main_async() -> None:
        if once:
            await runner.run_once(handler, send_fn=send_fn)
            return
        if not settings.telegram.token:
            console.print("telegram.token missing — running scheduler once only")
            await runner.run_once(handler, send_fn=send_fn)
            return
        adapter = TelegramAdapter(settings, hitl=hitl, store=store)
        console.print(
            f"[green]{brand_label()} gateway[/] polling Telegram "
            f"(allowlist={settings.telegram.allowlist or 'open'}) — Ctrl+C to stop"
        )
        console.print(f"[dim]logs → {log_path}[/]")
        logger.info(
            "gateway start allowlist=%s timezone=%s log=%s",
            settings.telegram.allowlist,
            settings.timezone,
            log_path,
        )

        async def sched_loop() -> None:
            while True:
                try:
                    await runner.run_once(handler, send_fn=send_fn)
                except Exception:
                    logger.exception("scheduler tick failed")
                await asyncio.sleep(30)

        sched_task = asyncio.create_task(sched_loop())
        try:
            await adapter.run(handler)
        finally:
            sched_task.cancel()
            lock.release()
            logger.info("gateway stopped")

    try:
        _run(main_async())
    finally:
        lock.release()


if __name__ == "__main__":
    app()
