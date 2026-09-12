"""Invariant runtime context is cached; only the clock and ledger stay volatile."""

from __future__ import annotations

from lattice.agent_app import build_prompt_bundle
from lattice.profiles import Profile
from lattice.prompt import build_runtime_context, build_runtime_notice


def test_runtime_context_contains_workspace_dbs_and_rules() -> None:
    context = build_runtime_context(
        workspace="/home/u/ws",
        timezone="Asia/Jakarta",
        databases=[("finance", "/home/u/ws/finance.db")],
        profile_id="default",
        preferred_skills=["sqlite-admin"],
        user_tools=["csv_stats"],
    )
    assert "/home/u/ws" in context
    assert "Asia/Jakarta" in context
    assert "finance -> /home/u/ws/finance.db" in context
    assert "sqlite_schema" in context
    assert "find /" in context
    assert len(context.splitlines()) <= 10


def test_runtime_notice_only_carries_the_clock() -> None:
    notice = build_runtime_notice(now="2026-09-12T18:00+07:00", timezone="Asia/Jakarta")
    assert "2026-09-12T18:00+07:00" in notice
    assert len(notice.splitlines()) <= 2


def test_invariant_runtime_context_is_cached_prefix_not_volatile() -> None:
    context = build_runtime_context(
        workspace="/x",
        timezone="UTC",
        databases=[],
        profile_id="default",
        preferred_skills=[],
        user_tools=[],
    )
    prompt = build_prompt_bundle(
        Profile(id="default"),
        [],
        ["[notice] Clock: now=2026"],
        runtime_context=context,
    )
    assert "workspace=/x" in prompt.stable_system_prompt()
    assert "workspace=/x" not in prompt.user_volatile_preamble()
    assert "Clock: now=2026" in prompt.user_volatile_preamble()
    assert "Clock: now=2026" not in prompt.stable_system_prompt()


def test_action_notice_is_volatile_not_cached_prefix() -> None:
    from lattice.action_ledger import ActionRecord
    from lattice.prompt import build_action_notice

    notice = build_action_notice([ActionRecord(tool="read_file", target="finance.py")])
    prompt = build_prompt_bundle(Profile(id="default"), [], [notice])
    assert "read_file finance.py" in prompt.user_volatile_preamble()
    assert "read_file finance.py" not in prompt.stable_system_prompt()
