"""Efficiency features: pressure calibration, discovery index, read elision,
bounded truncation, failure breaker, and summarizer reuse."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from lattice.config import LatticeSettings
from lattice.context import index as index_mod
from lattice.context.index import build_workspace_context
from lattice.context.pressure import PressureConfig
from lattice.deps import CORE_TOOL_NAMES, TurnDeps, traced, truncate_result
from lattice.events import NullTurnEvents
from lattice.hitl import AutoApproveHitl
from lattice.mcp import McpHostManager
from lattice.memory import InMemoryMemory
from lattice.profiles import ensure_default_profile, load_profile
from lattice.providers.summarizer import Summarizer
from lattice.session import SessionStore
from lattice.setup import write_skill_starters
from lattice.sqlite import SqlitePool, SqliteRegistry
from lattice.tools import read_cache
from lattice.tools.agent import default_eager_names, tool_functions


@pytest.fixture(autouse=True)
def _clean_read_cache():
    read_cache.reset()
    index_mod.reset_workspace_cache()
    yield
    read_cache.reset()
    index_mod.reset_workspace_cache()


def _deps(tmp_path: Path) -> TurnDeps:
    ensure_default_profile(tmp_path)
    write_skill_starters(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    profile = load_profile("default", tmp_path)
    registry = SqliteRegistry(settings, workspace=tmp_path)
    return TurnDeps(
        settings=settings,
        profile=profile,
        hitl=AutoApproveHitl(),
        session=SessionStore(tmp_path / "state.db"),
        session_id="s1",
        memory=InMemoryMemory("t"),
        sqlite_registry=registry,
        sqlite_pool=SqlitePool(registry),
        mcp=McpHostManager(),
        events=NullTurnEvents(),
        workspace=tmp_path,
        enabled_tools=list(CORE_TOOL_NAMES),
    )


def test_pressure_calibrates_against_observed_tokens() -> None:
    pressure = PressureConfig(ratio=0.5, model_context_tokens=1000, chars_per_token=4.0)
    messages = [{"role": "user", "content": "x" * 400}]
    assert pressure.is_over_pressure(messages) is False
    # Observed 1200 tokens for 400 chars (0.3 tok ≈ 3 tok/char) must trip it.
    assert pressure.is_over_pressure(messages, observed_tokens=1200, observed_chars=400) is True


@pytest.mark.asyncio
async def test_workspace_context_lists_files_and_db_schemas(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "note.txt").write_text("hi", encoding="utf-8")
    settings = LatticeSettings(home=tmp_path)
    registry = SqliteRegistry(settings, workspace=tmp_path)
    db = tmp_path / "fin.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    conn.commit()
    conn.close()
    registry.register("fin", db)
    pool = SqlitePool(registry)
    try:
        text = await build_workspace_context(tmp_path, registry, pool)
    finally:
        await pool.close_all()
    assert "Workspace index" in text
    assert "note.txt" in text
    assert "DB fin" in text
    assert "t(" in text


def test_workspace_index_cache_avoids_rescandir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    calls = {"n": 0}
    real = os.scandir

    def counting(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(index_mod.os, "scandir", counting)
    first = index_mod.workspace_index(tmp_path)
    second = index_mod.workspace_index(tmp_path)
    assert first == second
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_db_schema_is_cached_per_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lattice.context import index

    settings = LatticeSettings(home=tmp_path)
    registry = SqliteRegistry(settings, workspace=tmp_path)
    db = tmp_path / "fin.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.commit()
    conn.close()
    registry.register("fin", db)
    pool = SqlitePool(registry)

    calls = {"n": 0}
    real = index._introspect

    async def counting(entry, pool_arg, allow):
        calls["n"] += 1
        return await real(entry, pool_arg, allow)

    monkeypatch.setattr(index, "_introspect", counting)
    try:
        first = await index.database_schema_lines(registry, pool)
        second = await index.database_schema_lines(registry, pool)
    finally:
        await pool.close_all()
    assert first == second
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_read_file_elides_unchanged_body(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    tools = tool_functions()
    ctx = SimpleNamespace(deps=deps)

    first = await tools["read_file"](ctx, "a.txt")
    second = await tools["read_file"](ctx, "a.txt")
    assert first == "hello"
    assert "unchanged since last read" in second

    target.write_text("hello world", encoding="utf-8")
    third = await tools["read_file"](ctx, "a.txt")
    assert third == "hello world"

    # A new turn on the same session must re-serve the body: tool results are
    # not replayed, so the model has not seen this file this turn.
    deps.turn_id = "turn-2"
    fourth = await tools["read_file"](ctx, "a.txt")
    assert fourth == "hello world"
    fifth = await tools["read_file"](ctx, "a.txt")
    assert "unchanged since last read" in fifth


def test_truncate_result_default_unchanged() -> None:
    assert truncate_result("x" * 20_000).endswith("[truncated]")


def test_truncate_result_head_tail_with_scratch(tmp_path: Path) -> None:
    text = "A" * 20_000
    out = truncate_result(text, scratch_dir=tmp_path)
    assert out.startswith("A")
    assert "full result:" in out
    assert len(out) < len(text)
    assert list(tmp_path.glob("*.txt"))


@pytest.mark.asyncio
async def test_repeated_failure_breaker_adds_hint(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    ctx = SimpleNamespace(deps=deps)

    async def boom() -> str:
        raise ValueError("nope")

    first = await traced(ctx, "shell", {"command": "x"}, boom)
    second = await traced(ctx, "shell", {"command": "x"}, boom)
    third = await traced(ctx, "shell", {"command": "x"}, boom)
    assert "error: nope" in first
    assert "already failed twice" in second
    assert "breaker" in third


def test_summarizer_reuses_agent(tmp_path: Path) -> None:
    settings = LatticeSettings(home=tmp_path)
    model_id = "test-reuse-model-xyz"
    first = Summarizer(settings, model_id)
    second = Summarizer(settings, model_id)
    assert first._agent is second._agent


def test_eager_toolset_stays_bounded() -> None:
    # Schema bytes ride on every request; keep the eager set deliberately small.
    assert len(default_eager_names()) <= 16
    assert len(set(default_eager_names())) == len(default_eager_names())


def test_turn_record_reports_offered_tools() -> None:
    from lattice.turn_trace import LoggingTurnEvents

    trace = LoggingTurnEvents("offered1")
    trace.log_begin(
        channel="cli",
        user_id="u",
        profile_id="default",
        session_id="s",
        inbound_text="hi",
        tools=["read_file", "shell"],
        skills=[],
    )
    record = trace._build_record(duration_ms=1)
    assert record.tools_offered == 2
