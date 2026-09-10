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
