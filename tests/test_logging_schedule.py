"""Logging + schedule reminder tools."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lattice.logging_config import setup_logging
from lattice.models import Outbound
from lattice.scheduler import Job, SchedulerRunner, is_job_due, load_jobs, save_jobs
from lattice.scheduler.tools import schedule_add, schedule_cancel, schedule_list


def test_setup_logging_rotating_file(tmp_path: Path) -> None:
    path = setup_logging(tmp_path, force=True, also_stderr=False)
    assert path == tmp_path / "logs" / "lattice.log"
    logging.getLogger("lattice.test").info("hello-log")
    for h in logging.getLogger().handlers:
        h.flush()
    text = path.read_text(encoding="utf-8")
    assert "hello-log" in text


def test_schedule_add_run_at_and_list(tmp_path: Path) -> None:
    msg = schedule_add(
        reminder="Time to journal",
        home=tmp_path,
        run_at="2026-09-10T22:10:00+07:00",
        deliver="telegram",
        job_id="journal-1",
    )
    assert "scheduled journal-1" in msg
    listed = schedule_list(home=tmp_path)
    assert "journal-1" in listed and "Time to journal" in listed
    assert "cancelled" in schedule_cancel("journal-1", home=tmp_path)
    assert "(no scheduled jobs)" in schedule_list(home=tmp_path)


@pytest.mark.asyncio
async def test_run_at_delivers_message_without_llm(tmp_path: Path) -> None:
    save_jobs(
        [
            Job(
                id="j",
                prompt="ignored-llm",
                message="Journal now",
                schedule="@once",
                run_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                deliver="telegram",
                timezone="Asia/Bangkok",
            )
        ],
        tmp_path,
    )
    runner = SchedulerRunner(home=tmp_path)
    delivered: list[tuple[str, str]] = []

    async def turn_fn(inbound):
        raise AssertionError("message jobs must skip LLM")

    async def send_fn(channel: str, outbound: Outbound) -> None:
        delivered.append((channel, outbound.text))

    outs = await runner.run_once(turn_fn, send_fn=send_fn)
    assert outs == ["Journal now"]
    assert delivered == [("telegram", "Journal now")]
    assert load_jobs(tmp_path)[0].enabled is False


def test_cron_respects_job_timezone() -> None:
    # 22:10 Asia/Bangkok == 15:10 UTC
    utc_now = datetime(2026, 9, 10, 15, 10, tzinfo=UTC)
    job = Job(
        id="tz",
        prompt="x",
        schedule="10 22 * * *",
        timezone="Asia/Bangkok",
        enabled=True,
    )
    assert is_job_due(job, utc_now)
    assert not is_job_due(job, utc_now + timedelta(minutes=1))


def test_is_job_due_run_at_boundary() -> None:
    when = datetime(2026, 9, 10, 15, 10, tzinfo=UTC)
    job = Job(
        id="j",
        prompt="x",
        message="m",
        schedule="@once",
        run_at=when.isoformat(),
        timezone="UTC",
    )
    assert not is_job_due(job, when - timedelta(seconds=1))
    assert is_job_due(job, when)


def test_schedule_add_naive_run_at_uses_config_timezone(tmp_path: Path) -> None:
    from lattice.timeutil import persist_timezone

    persist_timezone("Asia/Ho_Chi_Minh", tmp_path)
    msg = schedule_add(
        reminder="Night routine",
        home=tmp_path,
        run_at="2026-09-10T22:45:00",
        deliver="telegram",
        job_id="night-1",
    )
    assert "2026-09-10T22:45:00+07:00" in msg
    jobs = load_jobs(tmp_path)
    assert jobs[0].timezone == "Asia/Ho_Chi_Minh"
    assert jobs[0].run_at == "2026-09-10T22:45:00+07:00"
    # Local 22:45+07 is due at 15:45 UTC
    assert is_job_due(jobs[0], datetime(2026, 9, 10, 15, 45, tzinfo=UTC))
    assert not is_job_due(jobs[0], datetime(2026, 9, 10, 15, 44, tzinfo=UTC))


def test_timezone_set_and_get(tmp_path: Path) -> None:
    from lattice.scheduler.tools import timezone_get, timezone_set

    assert "timezone saved" in timezone_set("Asia/Jakarta", home=tmp_path)
    assert "Asia/Jakarta" in timezone_get(home=tmp_path)
    assert (tmp_path / "lattice.yaml").is_file()


def test_models_come_from_lattice_yaml(tmp_path: Path) -> None:
    from lattice.config import load_settings, merge_yaml_into
    from lattice.providers.settings import (
        auxiliary_model_name,
        resolve_model_id,
        secondary_model_name,
    )

    merge_yaml_into(
        tmp_path / "lattice.yaml",
        {
            "agent": {
                "primary_model": "deepseek/deepseek-v4.1-flash",
                "secondary_model": "inception/mercury-2.5",
                "auxiliary_model": "inception/mercury-2.5",
            },
            "provider": {"fallback_model": "inception/mercury-2.5"},
        },
    )
    settings = load_settings(tmp_path)
    assert resolve_model_id(settings) == "deepseek/deepseek-v4.1-flash"
    assert secondary_model_name(settings) == "inception/mercury-2.5"
    assert auxiliary_model_name(settings) == "inception/mercury-2.5"
    assert settings.provider.fallback_model == "inception/mercury-2.5"


def test_legacy_agent_model_alias(tmp_path: Path) -> None:
    from lattice.config import load_settings, merge_yaml_into
    from lattice.providers.settings import resolve_model_id

    merge_yaml_into(tmp_path / "lattice.yaml", {"agent": {"model": "legacy/model"}})
    settings = load_settings(tmp_path)
    assert resolve_model_id(settings) == "legacy/model"


def test_secondary_refuses_nested_delegate(tmp_path: Path) -> None:
    import asyncio

    from lattice.agent_app import TurnDeps
    from lattice.agents.secondary import run_secondary
    from lattice.config import LatticeSettings
    from lattice.events import NullTurnEvents
    from lattice.hitl import AutoApproveHitl
    from lattice.mcp import McpHostManager
    from lattice.profiles.load import Profile
    from lattice.session import SessionStore
    from lattice.sqlite import SqlitePool, SqliteRegistry

    class _Mem:
        async def search(self, *a, **k):
            return []

        async def add(self, *a, **k):
            return "x"

        async def update(self, *a, **k):
            return None

        async def forget(self, *a, **k):
            return None

        async def sync_turn(self, *a, **k):
            return None

    settings = LatticeSettings(home=tmp_path)
    registry = SqliteRegistry(settings)
    deps = TurnDeps(
        settings=settings,
        profile=Profile(id="default"),
        hitl=AutoApproveHitl(approve_all=True),
        session=SessionStore(tmp_path / "state.db"),
        session_id="s",
        memory=_Mem(),  # type: ignore[arg-type]
        sqlite_registry=registry,
        sqlite_pool=SqlitePool(registry),
        mcp=McpHostManager(),
        events=NullTurnEvents(),
        workspace=tmp_path,
        enabled_tools=["web_search"],
        delegate_depth=1,
    )
    out = asyncio.run(run_secondary(deps, task="find x"))
    assert "nested" in out


def test_secondary_registers_all_primary_tools_except_delegate() -> None:
    from lattice.agents.secondary import SECONDARY_TOOL_NAMES, _build_secondary_agent
    from lattice.config import LatticeSettings
    from lattice.deps import CORE_TOOL_NAMES
    from lattice.tools.agent import build_toolsets, tool_functions

    primary_map = tool_functions()
    assert set(primary_map) == set(CORE_TOOL_NAMES)
    assert set(primary_map) - {"delegate"} == set(SECONDARY_TOOL_NAMES)

    settings = LatticeSettings(home=Path("/tmp/lattice-secondary-parity"))
    secondary = _build_secondary_agent(settings, "test")
    # The worker builds its toolsets per run (applying the primary's policy
    # ceiling), so no core tool is registered on the agent at construction time.
    builtin: set[str] = set()
    for ts in secondary.toolsets:
        builtin |= set(getattr(ts, "tools", {}).keys())
    assert not (builtin & set(CORE_TOOL_NAMES))

    # Names on the wire are the policy names (no `_tool` suffix).
    names = set(tool_functions(exclude=frozenset({"delegate"})))
    assert "read_file" in names
    assert "read_file_tool" not in names
    assert "delegate" not in names
    assert names == set(SECONDARY_TOOL_NAMES)

    # The toolset tree exposes exactly those names, eager and deferred alike.
    on_wire: set[str] = set()

    def _collect(ts: object) -> None:
        inner = getattr(ts, "wrapped", None)
        if inner is not None:
            _collect(inner)
        on_wire.update(getattr(ts, "tools", {}).keys())

    for ts in build_toolsets(exclude=frozenset({"delegate"})):
        _collect(ts)
    assert on_wire == set(SECONDARY_TOOL_NAMES)


def test_secondary_enabled_tools_derive_from_primary_policy() -> None:
    import asyncio

    from lattice.agent_app import TurnDeps
    from lattice.agents.secondary import run_secondary
    from lattice.config import LatticeSettings
    from lattice.events import NullTurnEvents
    from lattice.hitl import AutoApproveHitl
    from lattice.mcp import McpHostManager
    from lattice.profiles.load import Profile
    from lattice.session import SessionStore
    from lattice.sqlite import SqlitePool, SqliteRegistry

    class _Mem:
        async def search(self, *a, **k):
            return []

        async def add(self, *a, **k):
            return "x"

        async def update(self, *a, **k):
            return None

        async def forget(self, *a, **k):
            return None

        async def sync_turn(self, *a, **k):
            return None

    settings = LatticeSettings(home=Path("/tmp/lattice-secondary-policy"))
    registry = SqliteRegistry(settings)
    deps = TurnDeps(
        settings=settings,
        profile=Profile(id="default"),
        hitl=AutoApproveHitl(approve_all=True),
        session=SessionStore(Path("/tmp/lattice-secondary-policy/state.db")),
        session_id="s",
        memory=_Mem(),  # type: ignore[arg-type]
        sqlite_registry=registry,
        sqlite_pool=SqlitePool(registry),
        mcp=McpHostManager(),
        events=NullTurnEvents(),
        workspace=Path("/tmp/lattice-secondary-policy"),
        # Primary policy denies shell and includes delegate; neither may leak through.
        enabled_tools=["web_search", "delegate"],
        delegate_depth=0,
    )

    captured: dict[str, list[str]] = {}

    class _Agent:
        async def run(self, *a, **k):
            captured["enabled"] = list(k["deps"].enabled_tools)

            class _R:
                output = "ok"

            return _R()

    import lattice.agents.secondary as secondary

    original = secondary.get_secondary_agent
    secondary.get_secondary_agent = lambda s: _Agent()  # type: ignore[assignment]
    try:
        out = asyncio.run(run_secondary(deps, task="find x"))
    finally:
        secondary.get_secondary_agent = original  # type: ignore[assignment]

    assert out == "ok"
    assert captured["enabled"] == ["web_search"]


def test_secondary_gated_tool_is_hitl_gated_when_enabled(tmp_path: Path) -> None:
    import asyncio

    from pydantic_ai import RunContext

    from lattice.agent_app import TurnDeps
    from lattice.config import LatticeSettings
    from lattice.events import NullTurnEvents
    from lattice.hitl import ApprovalDecision, ApprovalRequest, AutoApproveHitl
    from lattice.mcp import McpHostManager
    from lattice.profiles.load import Profile
    from lattice.session import SessionStore
    from lattice.sqlite import SqlitePool, SqliteRegistry
    from lattice.tools.agent import tool_functions

    class _Mem:
        async def search(self, *a, **k):
            return []

        async def add(self, *a, **k):
            return "x"

        async def update(self, *a, **k):
            return None

        async def forget(self, *a, **k):
            return None

        async def sync_turn(self, *a, **k):
            return None

    class _DenyingHitl(AutoApproveHitl):
        def __init__(self) -> None:
            super().__init__(approve_all=True)
            self.seen: list[str] = []

        async def approve(self, req: ApprovalRequest) -> ApprovalDecision:
            self.seen.append(req.tool_name)
            return ApprovalDecision.DENY

    settings = LatticeSettings(home=tmp_path)
    registry = SqliteRegistry(settings)
    hitl = _DenyingHitl()
    deps = TurnDeps(
        settings=settings,
        profile=Profile(id="default"),
        hitl=hitl,
        session=SessionStore(tmp_path / "state.db"),
        session_id="s",
        memory=_Mem(),  # type: ignore[arg-type]
        sqlite_registry=registry,
        sqlite_pool=SqlitePool(registry),
        mcp=McpHostManager(),
        events=NullTurnEvents(),
        workspace=tmp_path,
        enabled_tools=["sqlite_unregister"],
    )
    ctx = RunContext(deps=deps, model=None, usage=None, prompt=None)  # type: ignore[arg-type]
    tool = tool_functions(exclude=frozenset({"delegate"}))["sqlite_unregister"]
    out = asyncio.run(tool(ctx, "notes"))
    assert out == "denied: deny"
    assert hitl.seen == ["sqlite_unregister"]


def test_session_dicts_to_history() -> None:
    from lattice.session_history import session_dicts_to_history

    hist = session_dicts_to_history(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "tool", "content": "ignored"},
        ]
    )
    assert len(hist) == 2
