"""Tool-result ergonomics: ranged reads, line-numbered search, truncation,
and the unified failure predicate (Track C)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from lattice.action_ledger import actions_from_messages
from lattice.config import LatticeSettings
from lattice.deps import CORE_TOOL_NAMES, TurnDeps, result_failed, traced
from lattice.events import NullTurnEvents
from lattice.hitl import AutoApproveHitl
from lattice.mcp import McpHostManager
from lattice.memory import InMemoryMemory
from lattice.profiles import ensure_default_profile, load_profile
from lattice.session import SessionStore
from lattice.setup import write_skill_starters
from lattice.sqlite import SqlitePool, SqliteRegistry
from lattice.tools import read_cache
from lattice.tools.file import search_files
from lattice.tools.groups import tool_functions


@pytest.fixture(autouse=True)
def _clean_read_cache():
    read_cache.reset()
    yield
    read_cache.reset()


def _deps(tmp_path: Path) -> TurnDeps:
    ensure_default_profile(tmp_path)
    write_skill_starters(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    profile = load_profile("default", tmp_path)
    registry = SqliteRegistry(settings, workspace=tmp_path)
    return TurnDeps(
        settings=settings,
        profile=profile,
        hitl=AutoApproveHitl(approve_all=True),
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


@pytest.mark.asyncio
async def test_ranged_read_then_default_read_is_not_elided(tmp_path: Path) -> None:
    body = "\n".join(f"line{i}" for i in range(10))
    (tmp_path / "a.txt").write_text(body, encoding="utf-8")
    deps = _deps(tmp_path)
    ctx = SimpleNamespace(deps=deps)
    tools = tool_functions()

    ranged = await tools["files/read"](ctx, "a.txt", 2, 3)
    assert "showing lines 3-5 of 10" in ranged
    assert "line2" in ranged
    assert "line4" in ranged
    assert "line5" not in ranged

    # A ranged read must not mark the whole file read: the default read still
    # returns the full body rather than an "unchanged" marker.
    full = await tools["files/read"](ctx, "a.txt")
    assert full == body


@pytest.mark.asyncio
async def test_search_files_returns_line_numbers(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("nothing here\n", encoding="utf-8")

    out = await search_files("beta", workspace=tmp_path)
    assert "src.py:2: beta" in out
    # A path match keeps the filename-only form.
    assert "beta.txt" in out.splitlines()


@pytest.mark.asyncio
async def test_long_output_traceback_survives_with_scratch_ref(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    ctx = SimpleNamespace(deps=deps)
    big = "\n".join(f"out-{i}-" + "x" * 40 for i in range(2000))
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "run.py", line 2, in <module>\n'
        "    raise ValueError('boom')\n"
        "ValueError: boom"
    )

    async def op() -> str:
        return f"exit=1 sandbox=soft path=/tmp/run.py\n{big}\n[stderr]\n{traceback}"

    result = await traced(ctx, "execute_script", {}, op)
    assert "ValueError: boom" in result
    assert "full result:" in result
    assert len(result) < len(big)


def test_failure_predicate_matches_producers() -> None:
    assert result_failed("error: boom")
    assert result_failed("  error: boom")
    assert result_failed("denied: nope")
    assert result_failed("web_search unavailable: no key")
    assert result_failed("error: web_search unavailable: no key")
    assert result_failed("browser unavailable: no chrome")
    assert not result_failed("ok")
    assert not result_failed("wrote /tmp/x")


@pytest.mark.asyncio
async def test_unavailable_result_trips_breaker(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    ctx = SimpleNamespace(deps=deps)

    async def unavailable() -> str:
        return "web_search unavailable: set tavily_api_key"

    first = await traced(ctx, "web_search", {"query": "x"}, unavailable)
    second = await traced(ctx, "web_search", {"query": "x"}, unavailable)
    third = await traced(ctx, "web_search", {"query": "x"}, unavailable)
    assert "already failed twice" in second
    assert "breaker" in third
    assert first == "web_search unavailable: set tavily_api_key"


def test_unavailable_result_persists_failed_in_ledger() -> None:
    messages = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="web_search", args={"query": "x"}, tool_call_id="1")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="web_search",
                    content="web_search unavailable: set tavily_api_key",
                    tool_call_id="1",
                )
            ]
        ),
    ]
    actions = actions_from_messages(messages)
    assert actions[0].ok is False


@pytest.mark.asyncio
async def test_web_search_missing_key_is_error_prefixed() -> None:
    from lattice.tools.web import web_search

    out = await web_search("query", api_key=None)
    assert out.startswith("error:")
    assert result_failed(out)
