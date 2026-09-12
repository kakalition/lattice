"""Per-turn runtime context lives in the volatile tail, not the cached prefix."""

from __future__ import annotations

from lattice.agent_app import build_prompt_bundle
from lattice.profiles import Profile
from lattice.prompt import build_runtime_notice


def test_runtime_notice_contains_workspace_time_and_dbs() -> None:
    notice = build_runtime_notice(
        workspace="/home/u/ws",
        now="2026-09-12T18:00+07:00",
        timezone="Asia/Jakarta",
        databases=[("finance", "/home/u/ws/finance.db")],
        profile_id="default",
        preferred_skills=["sqlite-admin"],
        user_tools=["csv_stats"],
    )
    assert "/home/u/ws" in notice
    assert "2026-09-12T18:00+07:00" in notice
    assert "Asia/Jakarta" in notice
    assert "finance -> /home/u/ws/finance.db" in notice
    assert "sqlite_schema" in notice
    assert "find /" in notice
    assert len(notice.splitlines()) <= 10


def test_notices_are_volatile_not_cached_prefix() -> None:
    prompt = build_prompt_bundle(
        Profile(id="default"),
        [],
        ["Runtime: workspace=/x; now=2026"],
    )
    assert "Runtime: workspace=/x" in prompt.user_volatile_preamble()
    assert "Runtime: workspace=/x" not in prompt.stable_system_prompt()
