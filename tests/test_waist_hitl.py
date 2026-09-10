"""Focused waist / HITL / policy / turn regression tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from lattice.agent_app import CORE_TOOL_NAMES, TurnDeps, maybe_approve, resolve_enabled_tools
from lattice.config import ChannelToolsConfig, LatticeSettings, TelegramConfig, ToolsConfig
from lattice.events import NullTurnEvents
from lattice.hitl import ApprovalDecision, ApprovalRequest, AutoApproveHitl
from lattice.hitl.telegram_adapter import TelegramHitlAdapter
from lattice.mcp import McpHostManager
from lattice.memory import InMemoryMemory
from lattice.models import Inbound
from lattice.profiles import Profile, ensure_default_profile, load_profile, merge_tool_policy
from lattice.providers import FallbackCooldown
from lattice.session import SessionStore
from lattice.setup import init_home, write_skill_starters
from lattice.sqlite import SqlitePool, SqliteRegistry
from lattice.tools.todo import TodoList
from lattice.turn import TurnCancelled, run_turn


@dataclass
class RecordingHitl:
    decision: ApprovalDecision = ApprovalDecision.DENY
    calls: list[str] = field(default_factory=list)

    async def approve(self, req: ApprovalRequest) -> ApprovalDecision:
        self.calls.append(req.tool_name)
        return self.decision

    async def clarify(self, req: Any) -> str:
        return "ok"


def _deps(tmp_path: Path, hitl: Any, *, enabled: list[str] | None = None) -> TurnDeps:
    ensure_default_profile(tmp_path)
    write_skill_starters(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    profile = load_profile("default", tmp_path)
    registry = SqliteRegistry(settings)
    return TurnDeps(
        settings=settings,
        profile=profile,
        hitl=hitl,
        session=SessionStore(tmp_path / "state.db"),
        session_id="s1",
        memory=InMemoryMemory("t"),
        sqlite_registry=registry,
        sqlite_pool=SqlitePool(registry),
        mcp=McpHostManager(),
        events=NullTurnEvents(),
        todos=TodoList(),
        workspace=tmp_path,
        enabled_tools=enabled or list(CORE_TOOL_NAMES),
        skills=[],
        cooldown=FallbackCooldown(),
    )


@pytest.mark.asyncio
async def test_maybe_approve_skips_safe_tools(tmp_path: Path) -> None:
    hitl = RecordingHitl(ApprovalDecision.APPROVE)
    deps = _deps(tmp_path, hitl)
    ctx = SimpleNamespace(deps=deps)
    assert await maybe_approve(ctx, "read_file", "x.txt") is None  # type: ignore[arg-type]
    assert hitl.calls == []


@pytest.mark.asyncio
async def test_maybe_approve_deny_and_memory(tmp_path: Path) -> None:
    hitl = RecordingHitl(ApprovalDecision.DENY)
    deps = _deps(tmp_path, hitl)
    ctx = SimpleNamespace(deps=deps)
    denied = await maybe_approve(ctx, "sqlite_execute", "a", name="a", sql="DELETE FROM t")  # type: ignore[arg-type]
    assert denied == "denied: deny"
    assert hitl.calls == ["sqlite_execute"]

    hitl.decision = ApprovalDecision.APPROVE
    assert (
        await maybe_approve(ctx, "sqlite_execute", "b", name="b", sql="DELETE FROM t")  # type: ignore[arg-type]
        is None
    )
    assert (
        await maybe_approve(ctx, "sqlite_execute", "b", name="b", sql="DELETE FROM t")  # type: ignore[arg-type]
        is None
    )
    assert hitl.calls.count("sqlite_execute") == 2


@pytest.mark.asyncio
async def test_maybe_approve_consecutive_denial_breaker(tmp_path: Path) -> None:
    hitl = RecordingHitl(ApprovalDecision.DENY)
    deps = _deps(tmp_path, hitl)
    ctx = SimpleNamespace(deps=deps)
    for i in range(2):
        out = await maybe_approve(
            ctx, "sqlite_execute", f"f{i}", name=f"f{i}", sql="DELETE FROM t"
        )  # type: ignore[arg-type]
        assert out == "denied: deny"
    out = await maybe_approve(ctx, "sqlite_execute", "f3", name="f3", sql="DELETE FROM t")  # type: ignore[arg-type]
    assert out == "denied (consecutive denial breaker)"


def test_tool_policy_matrix_deny_and_channel() -> None:
    names = list(CORE_TOOL_NAMES)
    restricted = merge_tool_policy(
        names,
        profile_allow=["sqlite_*", "web_*", "read_file", "clarify", "memory_*", "skill*"],
        profile_deny=["shell", "write_file", "edit_file"],
        channel_allow=["*"],
        channel_deny=[],
    )
    assert "shell" not in restricted
    assert "write_file" not in restricted
    assert "sqlite_query" in restricted
    assert "web_search" in restricted
    assert "skill_view" in restricted

    telegram_tight = merge_tool_policy(
        names,
        profile_allow=["*"],
        profile_deny=[],
        channel_allow=["read_file", "web_*", "clarify"],
        channel_deny=["shell"],
    )
    assert "shell" not in telegram_tight
    assert "read_file" in telegram_tight
    assert "web_fetch" in telegram_tight
    assert "write_file" not in telegram_tight


def test_resolve_enabled_tools_respects_telegram_deny(tmp_path: Path) -> None:
    ensure_default_profile(tmp_path)
    settings = LatticeSettings(
        home=tmp_path,
        telegram=TelegramConfig(
            tools=ChannelToolsConfig(allow=["*"], deny=["shell", "write_file"])
        ),
        tools=ToolsConfig(allow=["*"], deny=[]),
    )
    profile = Profile(id="default", tools_allow=["*"], tools_deny=[])
    enabled = resolve_enabled_tools(settings, profile, channel="telegram", mcp=McpHostManager())
    assert "shell" not in enabled
    assert "write_file" not in enabled
    assert "read_file" in enabled


@pytest.mark.asyncio
async def test_run_turn_with_test_model(tmp_path: Path) -> None:
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    store = SessionStore(tmp_path / "state.db")
    out = await run_turn(
        Inbound(text="hello", profile_id="default", channel="cli", user_id="u"),
        settings=settings,
        hitl=AutoApproveHitl(approve_all=True),
        session_store=store,
        model=TestModel(call_tools=[], custom_output_text="test-model-ok"),
    )
    assert out.text == "test-model-ok"
    assert out.session_id
    saved = await store.get(out.session_id)
    assert saved is not None
    assert saved["messages"][-1]["content"] == "test-model-ok"
    assert saved["messages"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_run_turn_cancel_flag(tmp_path: Path) -> None:
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    with pytest.raises(TurnCancelled):
        await run_turn(
            Inbound(text="x", cancel=True, profile_id="default"),
            settings=settings,
            model=TestModel(call_tools=[], custom_output_text="nope"),
        )


@pytest.mark.asyncio
async def test_run_turn_steer_persisted(tmp_path: Path) -> None:
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    store = SessionStore(tmp_path / "state.db")
    out = await run_turn(
        Inbound(
            text="main",
            steer_text="prefer bullets",
            profile_id="default",
            channel="cli",
        ),
        settings=settings,
        session_store=store,
        model=TestModel(call_tools=[], custom_output_text="steered"),
    )
    saved = await store.get(out.session_id or "")
    assert saved is not None
    assert "[steer] prefer bullets" in saved["messages"][0]["content"]


@pytest.mark.asyncio
async def test_telegram_hitl_resolve_approve() -> None:
    adapter = TelegramHitlAdapter(timeout_seconds=2)
    sent: list[dict] = []

    async def send_fn(*, text: str, buttons: list | None = None) -> None:
        sent.append({"text": text, "buttons": buttons})

    adapter.bind_send(send_fn)
    task = asyncio.create_task(
        adapter.approve(ApprovalRequest(tool_name="shell", summary="rm -rf /tmp/x"))
    )
    for _ in range(50):
        if adapter._pending:
            break
        await asyncio.sleep(0.01)
    assert adapter._pending
    token = next(iter(adapter._pending))
    assert adapter.resolve(token, "approve") is True
    assert adapter.resolve(token, "approve") is False
    decision = await task
    assert decision == ApprovalDecision.APPROVE
    assert sent and "shell" in sent[0]["text"]
    assert "A — Approve" in sent[0]["text"]
    assert sent[0]["buttons"][0]["label"] == "A"
    assert sent[0]["buttons"][1]["label"] == "B"


@pytest.mark.asyncio
async def test_telegram_clarify_letter_buttons() -> None:
    from lattice.hitl.base import ClarifyRequest

    adapter = TelegramHitlAdapter(timeout_seconds=2)
    sent: list[dict] = []

    async def send_fn(*, text: str, buttons: list | None = None) -> None:
        sent.append({"text": text, "buttons": buttons})

    long_a = "Create a full personal-finance ledger with accounts and categories"
    long_b = "Only track cash expenses for now"
    adapter.bind_send(send_fn)
    task = asyncio.create_task(
        adapter.clarify(
            ClarifyRequest(question="How should we set up the ledger?", choices=[long_a, long_b])
        )
    )
    for _ in range(50):
        if adapter._pending:
            break
        await asyncio.sleep(0.01)
    token = next(iter(adapter._pending))
    assert sent[0]["buttons"] == [
        {"label": "A", "data": f"hitl:{token}:c0"},
        {"label": "B", "data": f"hitl:{token}:c1"},
    ]
    assert f"A. {long_a}" in sent[0]["text"]
    assert f"B. {long_b}" in sent[0]["text"]
    assert adapter.resolve(token, "c1")
    assert await task == long_b


@pytest.mark.asyncio
async def test_telegram_hitl_cancel_all() -> None:
    adapter = TelegramHitlAdapter(timeout_seconds=5)

    async def send_fn(*, text: str, buttons: list | None = None) -> None:
        return None

    adapter.bind_send(send_fn)
    task = asyncio.create_task(
        adapter.approve(ApprovalRequest(tool_name="shell", summary="rm"))
    )
    for _ in range(50):
        if adapter._pending:
            break
        await asyncio.sleep(0.01)
    assert adapter.cancel_all("cancel") == 1
    assert await task == ApprovalDecision.CANCELLED

@pytest.mark.asyncio
async def test_telegram_hitl_timeout() -> None:
    adapter = TelegramHitlAdapter(timeout_seconds=0)
    decision = await adapter.approve(ApprovalRequest(tool_name="shell", summary="x"))
    assert decision == ApprovalDecision.TIMEOUT


@pytest.mark.asyncio
async def test_telegram_clarify_free_text_while_busy() -> None:
    from lattice.hitl.base import ClarifyRequest

    adapter = TelegramHitlAdapter(timeout_seconds=2)
    sent: list[str] = []

    async def send_fn(*, text: str, buttons: list | None = None) -> None:
        sent.append(text)

    adapter.bind_send(send_fn)
    adapter.set_active_user("42")
    task = asyncio.create_task(
        adapter.clarify(ClarifyRequest(question="What timezone?", choices=[]))
    )
    for _ in range(50):
        if adapter.awaiting_text("42"):
            break
        await asyncio.sleep(0.01)
    assert adapter.awaiting_text("42")
    assert adapter.resolve_text("42", "Asia/Bangkok")
    assert await task == "Asia/Bangkok"
    assert sent and "timezone" in sent[0].lower()
