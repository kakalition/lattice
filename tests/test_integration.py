"""Live integration checks — skipped unless keys present.

Run with: uv run pytest -m integration
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lattice.config import load_settings
from lattice.hitl import AutoApproveHitl
from lattice.models import Inbound
from lattice.setup import init_home
from lattice.tools.web import web_search
from lattice.turn import run_turn


def _has_llm() -> bool:
    load_settings()  # loads project .env
    return bool(
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LATTICE_PROVIDER__API_KEY")
    )


def _has_tavily() -> bool:
    load_settings()
    return bool(os.environ.get("TAVILY_API_KEY") or os.environ.get("LATTICE_TAVILY_API_KEY"))


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_llm(), reason="no LLM API key")
async def test_live_run_turn_smoke(tmp_path: Path) -> None:
    settings = load_settings()
    # isolate sessions under tmp while keeping provider env
    settings.home = tmp_path
    init_home(tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    out = await run_turn(
        Inbound(text="Reply with exactly: integration-ok", profile_id="default", channel="cli"),
        settings=settings,
        hitl=AutoApproveHitl(approve_all=True),
    )
    assert "integration-ok" in out.text.lower() or "ok" in out.text.lower()


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_tavily(), reason="no Tavily API key")
async def test_live_tavily_search() -> None:
    settings = load_settings()
    text = await web_search("Python asyncio", api_key=settings.tavily_api_key, max_results=2)
    assert "untrusted" in text
    assert "http" in text.lower()


def _has_telegram() -> bool:
    load_settings()
    return bool(os.environ.get("TELEGRAM_TOKEN") or os.environ.get("LATTICE_TELEGRAM__TOKEN"))


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_telegram(), reason="no Telegram token")
async def test_live_telegram_polling_lifecycle() -> None:
    """Start/stop PTB updater inside a running event loop (the gateway bug class)."""
    import asyncio

    from telegram import Update
    from telegram.ext import Application

    settings = load_settings()
    token = settings.telegram.token
    assert token
    app = Application.builder().token(token).build()
    await app.initialize()
    await app.start()
    assert app.updater is not None
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    me = await app.bot.get_me()
    assert me.username
    await asyncio.sleep(0.5)
    await app.updater.stop()
    await app.stop()
    await app.shutdown()


# --- tool tiering / deferral against a real provider -----------------------


def _live_settings(tmp_path: Path):
    """Real lattice.yaml + .env, but isolated runtime state."""
    from lattice.setup import write_skill_starters

    settings = load_settings()
    settings.home = tmp_path
    init_home(tmp_path)
    write_skill_starters(tmp_path)
    settings.agent.workspace = tmp_path
    return settings


async def _run_live(tmp_path: Path, allowed: list[str], prompt: str):
    """Run one real turn and return (result, tool names called)."""
    from pydantic_ai import Agent
    from pydantic_ai.messages import ToolCallPart
    from tests.test_toolsets_tiers import _deps_for

    from lattice.agent_app import (
        build_core_toolset,
        tool_search_capability,
    )
    from lattice.deps import TurnDeps
    from lattice.mcp import McpHostManager
    from lattice.profiles import get_profile
    from lattice.providers.openai_compat import build_openai_model
    from lattice.providers.settings import resolve_model_id

    settings = _live_settings(tmp_path)
    mcp = McpHostManager()
    profile = get_profile("default", settings.home)
    model_id = resolve_model_id(settings, profile_model=profile.primary_model)

    deps = _deps_for(settings, allowed)
    agent = Agent(
        build_openai_model(settings, model_id),
        deps_type=TurnDeps,
        system_prompt=(
            "You are Lattice, a personal agent. Use your tools. If a capability you "
            "need is not in your current tool list, use search_tools to find it."
        ),
        toolsets=[build_core_toolset(settings, mcp)],
        capabilities=[tool_search_capability()],
    )
    try:
        result = await agent.run(prompt, deps=deps)
    finally:
        await deps.sqlite_pool.close_all()

    called = [
        p.tool_name
        for m in result.all_messages()
        for p in getattr(m, "parts", [])
        if isinstance(p, ToolCallPart)
    ]
    return result, called


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_llm(), reason="no LLM API key")
async def test_live_cold_tool_is_discovered_and_executed(tmp_path: Path) -> None:
    """A deferred tool must be findable and runnable on a real model.

    `schedule_add` is cold (not in the eager set), so the only way to reach it is
    through `search_tools`. This is the end-to-end proof that deferral is a
    prompt-size optimisation and not a capability regression.
    """
    from lattice.agent_app import resolve_enabled_tools
    from lattice.mcp import McpHostManager
    from lattice.profiles import get_profile

    settings = _live_settings(tmp_path)
    profile = get_profile("default", settings.home)
    enabled = resolve_enabled_tools(settings, profile, channel="cli", mcp=McpHostManager())
    assert "schedule/add" in enabled

    _, called = await _run_live(
        tmp_path,
        enabled,
        "Schedule a reminder to buy milk tomorrow at 9am.",
    )
    assert "search_tools" in called, "model never searched for the cold tool"
    assert "schedule__add" in called, "cold tool was not executable after discovery"
    assert (tmp_path / "scheduler" / "jobs.json").exists()


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_llm(), reason="no LLM API key")
async def test_live_eager_tool_needs_no_discovery_round_trip(tmp_path: Path) -> None:
    """Tiering must not over-defer: an eager tool is callable immediately."""
    _, called = await _run_live(
        tmp_path,
        ["interaction/todo", "files/read", "search_tools"],
        "Add 'buy milk' to my todo list.",
    )
    assert "interaction__todo" in called
    assert "search_tools" not in called, "eager tool wrongly required discovery"


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_llm(), reason="no LLM API key")
async def test_live_search_cannot_bypass_policy(tmp_path: Path) -> None:
    """The security property: discovery must not reveal a denied tool.

    `timezone_get` (allowed, cold) keeps the search corpus non-empty so
    `search_tools` is actually offered. `schedule_add` is denied and must stay
    invisible and unrunnable.
    """
    _, called = await _run_live(
        tmp_path,
        ["files/read", "schedule/timezone_get"],  # schedule/add deliberately withheld
        "Schedule a reminder to buy milk tomorrow at 9am. Search for a tool if needed.",
    )
    assert "schedule__add" not in called, "denied cold tool was reachable via search"
