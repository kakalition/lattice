"""Typer CLI entrypoint."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path

import typer
from rich.console import Console

from lattice import __version__
from lattice.backup import create_backup, default_backup_path, gateway_running, restore_backup
from lattice.branding import brand_label
from lattice.config import load_settings
from lattice.hitl import AutoApproveHitl, CliHitlAdapter
from lattice.logging_config import setup_logging
from lattice.oneshot import ensure_oneshot_setup
from lattice.paths import lattice_home
from lattice.providers.settings import resolve_api_key
from lattice.setup import doctor_report, init_home
from lattice.turn import echo_turn, run_turn

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Lattice personal agent")
eval_app = typer.Typer(no_args_is_help=True, help="Offline eval + replay (no secrets)")
app.add_typer(eval_app, name="eval")
console = Console()
logger = logging.getLogger("lattice.cli")


def _run(coro):
    return asyncio.run(coro)


def _drain_memory() -> None:
    """Flush any queued background memory writes after the loop has closed."""
    from lattice.memory.worker import drain_memory_sync

    drain_memory_sync()


def _boot_memory_self_check(settings, profile: str) -> None:
    """Round-trip the memory backend at boot; abort loudly if it is broken.

    A memory search that silently returns nothing is worse than a crash: the
    agent still answers, just without ever recalling anything. Fail fast instead.
    """
    if not settings.memory.self_check:
        logger.info("memory self-check disabled by config")
        return
    from lattice.agent_app import verify_memory_for_profile
    from lattice.profiles import get_profile

    try:
        notes = verify_memory_for_profile(settings, get_profile(profile, settings.home))
    except Exception as exc:
        console.print(f"[red]memory self-check failed:[/] {exc}")
        console.print(
            "[dim]Memory would silently return no results. Fix the backend, or set "
            "`memory.self_check: false` in lattice.yaml to boot without it.[/]"
        )
        raise typer.Exit(1) from exc
    for note in notes:
        logger.info("%s", note)


@app.callback()
def main() -> None:
    """Lattice — thin-waist personal agent."""


@app.command()
def version() -> None:
    """Show version."""
    console.print(__version__)


@app.command("init")
def init_cmd(
    home: Path | None = typer.Option(
        None, help="Override Lattice home (default <project>/.lattice)"
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Archive the current home (if non-empty), wipe it, then re-initialize.",
    ),
) -> None:
    """Create <project>/.lattice layout, default profile, and skill starters.

    ``--reset`` starts fresh: the existing home is archived to
    ``<home-name>-<utc>.tar.gz`` in the current directory, then removed and
    re-initialized. Secrets live in the project ``.env`` and are untouched.
    """
    root = (home or lattice_home()).resolve()
    archived: Path | None = None
    if reset:
        running = gateway_running(root)
        if running is not None:
            console.print(f"[red]gateway is running (pid {running}); stop it before --reset[/]")
            raise typer.Exit(1)
        if root.is_dir() and any(root.iterdir()):
            out = default_backup_path(root)
            try:
                result = create_backup(root, output=out)
            except FileExistsError as exc:
                console.print(f"[red]{exc}[/]")
                raise typer.Exit(1) from exc
            archived = result.archive
        shutil.rmtree(root, ignore_errors=True)
    root = init_home(home)
    setup_logging()
    if archived is not None:
        console.print(f"[yellow]archived[/] previous home → {archived}")
    console.print(f"initialized {root}")


@app.command()
def doctor(
    home: Path | None = typer.Option(None, help="Override Lattice home"),
) -> None:
    """Diagnose config / keys / profiles."""
    load_settings(home)
    setup_logging()
    for line in doctor_report(home):
        console.print(line)


@app.command()
def backup(
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Archive path (default ./<home-name>-<utc>.tar.gz)",
    ),
    home: Path | None = typer.Option(None, help="Override Lattice home"),
    include_logs: bool = typer.Option(
        False,
        "--include-logs",
        help="Include .lattice/logs (omitted by default)",
    ),
) -> None:
    """Compile .lattice into a portable .tar.gz archive (backup)."""
    root = home or lattice_home()
    out = output or default_backup_path(root)
    try:
        result = create_backup(root, output=out, include_logs=include_logs)
    except (FileNotFoundError, FileExistsError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]compiled[/] {result.home} → {result.archive} "
        f"({result.file_count} files, {result.bytes} bytes)"
    )


@app.command()
def restore(
    archive: Path = typer.Argument(..., help="Path to lattice-home-*.tar.gz"),
    home: Path | None = typer.Option(None, help="Override Lattice home"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Displace existing home to <.lattice>.bak.<utc> if non-empty",
    ),
) -> None:
    """Restore a compiled .lattice archive into the home directory."""
    root = home or lattice_home()
    try:
        result = restore_backup(archive, root, force=force)
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    msg = f"[green]restored[/] {result.archive} → {result.home} ({result.file_count} files)"
    if result.displaced is not None:
        msg += f"\n[dim]previous home moved to {result.displaced}[/]"
    console.print(msg)


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
    ensure_oneshot_setup(settings.home, console=console)
    _boot_memory_self_check(settings, profile)

    # One store for the whole session: the adapter and run_turn must not open a
    # separate connection (and re-run schema DDL) per turn.
    from lattice.session import SessionStore

    store = SessionStore(settings.home / "state.db")

    async def handler(inbound):
        inbound.profile_id = inbound.profile_id or profile
        if echo or not resolve_api_key(settings):
            if not echo and not resolve_api_key(settings):
                console.print("[yellow]No API key — using echo mode. Set OPENAI_API_KEY.[/]")
            return await echo_turn(inbound)
        hitl = CliHitlAdapter(timeout_seconds=settings.agent.hitl_timeout_seconds)
        return await run_turn(
            inbound,
            settings=settings,
            hitl=hitl,
            session_store=store,
            cancel_event=inbound.cancel_event,
        )

    if tui:
        from lattice.channel.cli.tui import run_tui

        try:
            _run(run_tui(handler, profile_id=profile))
        finally:
            _drain_memory()
        return

    from lattice.channel.cli.adapter import CliAdapter

    adapter = CliAdapter(profile_id=profile, store=store)
    try:
        _run(adapter.run(handler))
    finally:
        _drain_memory()


@eval_app.command("run")
def eval_run(
    corpus: Path | None = typer.Option(
        None, "--corpus", help="Corpus directory (default tests/eval/corpus)"
    ),
    json_out: Path | None = typer.Option(
        None, "--json", help="Report path (default <home>/evals/<ts>.json)"
    ),
    home: Path | None = typer.Option(None, help="Override Lattice home"),
) -> None:
    """Replay the corpus through production run_turn; exit non-zero on failure."""
    from datetime import UTC, datetime

    from lattice.eval.runner import run_eval
    from lattice.paths import project_root

    settings = load_settings(home)
    init_home(settings.home)
    corpus_dir = corpus or (project_root() / "tests" / "eval" / "corpus")
    if not corpus_dir.is_dir():
        console.print(f"[red]corpus directory not found:[/] {corpus_dir}")
        raise typer.Exit(1)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = json_out or (settings.home / "evals" / f"{ts}.json")
    report = _run(run_eval(corpus_dir, home=settings.home, output=output))
    for row in report.rows:
        status = "skip" if row.skipped else ("pass" if row.passed else "FAIL")
        color = {"pass": "green", "FAIL": "red", "skip": "yellow"}[status]
        console.print(f"[{color}]{status}[/] {row.id} tools={row.tools} drift={row.prompt_drift}")
        if not row.passed and not row.skipped:
            for assertion in row.assertions:
                if not assertion.passed:
                    console.print(f"    [red]x[/] {assertion.name}: {assertion.detail}")
    console.print(
        f"passed={report.passed} failed={report.failed} skipped={report.skipped} → {output}"
    )
    if report.failed:
        raise typer.Exit(1)


@eval_app.command("mine")
def eval_mine(
    log: Path | None = typer.Option(None, "--log", help="Path to lattice.log"),
    audit: Path | None = typer.Option(None, "--audit", help="Path to audit.jsonl"),
    out: Path | None = typer.Option(None, "--out", help="Corpus directory to write"),
    home: Path | None = typer.Option(None, help="Override Lattice home"),
    limit: int | None = typer.Option(None, "--limit", help="Max rows to mine"),
) -> None:
    """Mine draft corpus rows from a real log (plus shell commands from audit)."""
    from lattice.eval.corpus import dump_corpus, mine_log, mine_shell_commands

    root = home or lattice_home()
    log_path = log or (root / "logs" / "lattice.log")
    audit_path = audit or (root / "audit.jsonl")
    out_dir = out or (root / "eval-corpus")
    rows = mine_log(log_path, limit=limit)
    dump_corpus(rows, out_dir / "mined.jsonl")
    commands = mine_shell_commands(audit_path)
    if commands:
        (out_dir / "shell-guard.txt").write_text("\n".join(commands), encoding="utf-8")
    console.print(
        f"mined {len(rows)} draft row(s) → {out_dir / 'mined.jsonl'} "
        f"({len(commands)} shell command(s) → shell-guard.txt)"
    )


@app.command()
def stats(
    days: int = typer.Option(0, "--days", help="Only the last N days (0 = all)"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON"),
    home: Path | None = typer.Option(None, help="Override Lattice home"),
) -> None:
    """Aggregate <home>/logs/turns.jsonl into outcome/latency/cache stats."""
    import json as jsonlib

    from lattice.stats import stats_for_home
    from lattice.turn_record import turn_records_path

    root = home or lattice_home()
    path = turn_records_path(root)
    data = stats_for_home(root, days=days or None)
    if as_json:
        console.print_json(jsonlib.dumps(data))
        return
    console.print(f"[bold]turns[/] {data['turns']}  [dim]{path}[/]")
    if not data["turns"]:
        return
    console.print("outcomes: " + ", ".join(f"{k}={v}" for k, v in data["outcomes"].items()))
    duration = data["duration_ms"]
    ttft = data["ttft_ms"]
    console.print(
        f"duration_ms p50={duration['p50']} p95={duration['p95']}  "
        f"ttft_ms p50={ttft['p50']} p95={ttft['p95']}"
    )
    for name, phase in data.get("phases", {}).items():
        console.print(f"{name}_ms p50={phase['p50']} p95={phase['p95']} total={phase['total_ms']}")
    console.print(
        f"requests={data['requests']} tool_calls={data['tool_calls']} "
        f"cache_hit_ratio={data['cache_hit_ratio']:.3f} "
        f"retries={data['retries']} compressions={data['compressions']}"
    )
    if data["top_failing_tools"]:
        console.print(
            "top failing: "
            + ", ".join(f"{name}={count}" for name, count in data["top_failing_tools"])
        )
    if data["top_expensive_tools"]:
        console.print(
            "top expensive: "
            + ", ".join(f"{name}={ms}ms" for name, ms in data["top_expensive_tools"])
        )


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
    ensure_oneshot_setup(settings.home, console=console)
    _boot_memory_self_check(settings, settings.default_profile)
    lock = PidfileLock(settings.home / "gateway.pid")
    lock.acquire()
    store = SessionStore(settings.home / "state.db")
    hitl = TelegramHitlAdapter(timeout_seconds=settings.agent.hitl_timeout_seconds)
    runner = SchedulerRunner(home=settings.home)

    async def handler(inbound):
        if not resolve_api_key(settings):
            return await echo_turn(inbound)
        local_hitl = hitl if inbound.channel == "telegram" else AutoApproveHitl(approve_all=False)
        return await run_turn(
            inbound,
            settings=settings,
            hitl=local_hitl,
            session_store=store,
            cancel_event=inbound.cancel_event,
        )

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
            from lattice.memory.worker import flush_memory

            with contextlib.suppress(Exception):
                await flush_memory()
            lock.release()
            logger.info("gateway stopped")

    try:
        _run(main_async())
    finally:
        lock.release()
        _drain_memory()


if __name__ == "__main__":
    app()
