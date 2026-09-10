"""Unit tests for Lattice waist and domains."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lattice.config import LatticeSettings, SqliteConfig, SqliteDatabaseConfig
from lattice.context import PressureConfig, compress
from lattice.hitl.policies import shell_needs_approval, tool_needs_approval
from lattice.models import Inbound
from lattice.profiles import ensure_default_profile, load_profile, merge_tool_policy
from lattice.prompt import PromptBundle, build_skill_index_xml
from lattice.providers.errors import FailoverReason, classify_provider_error, recovery_action
from lattice.providers.fallback_cooldown import FallbackCooldown
from lattice.session import SessionStore, sanitize_messages
from lattice.setup import init_home, write_skill_starters
from lattice.skills import scan_skills, skill_index_entries, skill_view
from lattice.sqlite import SqliteRegistry
from lattice.tools.file_safety import PathDeniedError, resolve_in_workspace
from lattice.turn import echo_turn


def test_echo_turn() -> None:
    out = asyncio.run(echo_turn(Inbound(text="hi", profile_id="default")))
    assert "hi" in out.text


def test_merge_tool_policy_deny_wins() -> None:
    names = ["shell", "read_file", "sqlite_query", "write_file"]
    got = merge_tool_policy(
        names,
        profile_allow=["sqlite_*", "read_file", "web_*"],
        profile_deny=["shell", "write_file"],
        channel_allow=["*"],
        channel_deny=[],
    )
    assert got == ["read_file", "sqlite_query"]


def test_path_deny(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ok.txt").write_text("x", encoding="utf-8")
    assert resolve_in_workspace("ok.txt", ws).name == "ok.txt"
    with pytest.raises(PathDeniedError):
        resolve_in_workspace(str(tmp_path / "secret.env"), ws)


def test_shell_approval_patterns() -> None:
    assert shell_needs_approval("rm -rf /tmp/x")
    assert not shell_needs_approval("ls -la")
    assert tool_needs_approval("write_file")
    assert not tool_needs_approval("read_file")


def test_prompt_bundle_stable() -> None:
    b = PromptBundle(identity="I am Lattice", skill_index=build_skill_index_xml([("a", "b")]))
    assert b.system_prompt() == b.stable_system_prompt()
    assert b.system_prompt() == b.system_prompt()
    assert "available_skills" in b.system_prompt()
    assert "Orchestration" in b.stable_system_prompt()


def test_prompt_notices_are_volatile_only() -> None:
    b = PromptBundle(identity="I am Lattice", notices=["memory hit"])
    assert "[notice]" not in b.stable_system_prompt()
    assert "memory hit" in b.user_volatile_preamble()
    b2 = PromptBundle(identity="I am Lattice", notices=["other"])
    assert b.stable_system_prompt() == b2.stable_system_prompt()


def test_sanitize_messages() -> None:
    msgs = [
        {"role": "assistant", "tool_calls": [{"id": "t1", "function": {"name": "x"}}]},
        {"role": "user", "content": "hi"},
    ]
    cleaned = sanitize_messages(msgs)
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "t1" for m in cleaned)


def test_error_taxonomy() -> None:
    assert classify_provider_error(RuntimeError("429 rate limit")) == FailoverReason.RATE_LIMIT
    assert recovery_action(FailoverReason.CONTEXT_OVERFLOW) == "compress"
    assert recovery_action(FailoverReason.AUTH) == "abort"


def test_fallback_cooldown() -> None:
    c = FallbackCooldown(base_seconds=0.01, max_seconds=0.05)
    assert c.primary_allowed()
    c.mark_fallback()
    c.clear()
    assert c.primary_allowed()


def test_init_and_skills(tmp_path: Path) -> None:
    root = init_home(tmp_path)
    assert (root / "lattice.yaml").exists()
    assert (root / "profiles" / "default" / "SOUL.md").exists()
    assert (root / "profiles" / "finance" / "profile.yaml").exists()
    write_skill_starters(root)
    skills = scan_skills(root)
    names = {s.name for s in skills}
    assert "sqlite-admin" in names
    assert "cited-research" in names
    assert "weekly-review" in names
    assert "office-xlsx" in names
    assert "telegram-chat" in names
    entries = skill_index_entries(
        skills, prefer=["telegram-chat", "sqlite-admin"], disable=["safe-shell"]
    )
    assert entries[0][0] == "telegram-chat"
    body = skill_view("sqlite-admin", skills)
    assert "sqlite_backup" in body
    cited = skill_view("cited-research", skills)
    assert "Sources:" in cited
    tg = skill_view("telegram-chat", skills)
    assert "table" in tg.lower()
    profile = load_profile("finance", root)
    assert "shell" in profile.tools_deny
    assert "cited-research" in profile.skills_prefer
    assert "telegram-chat" in profile.skills_prefer


@pytest.mark.asyncio
async def test_session_store(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state.db")
    sid = await store.create(profile_id="default", user_id="u", channel="cli")
    await store.save_messages(sid, [{"role": "user", "content": "hello world"}])
    got = await store.get(sid)
    assert got is not None
    assert got["messages"][0]["content"] == "hello world"
    await store.set_sticky_profile("telegram", "1", "finance")
    assert await store.get_sticky_profile("telegram", "1") == "finance"
    hits = await store.search("hello")
    assert hits


@pytest.mark.asyncio
async def test_compress_noop() -> None:
    msgs = [{"role": "user", "content": "hi"}]
    result = await compress(msgs, aux=None, protect_last_n=20)
    assert not result.compressed


@pytest.mark.asyncio
async def test_compress_trim_fallback() -> None:
    pressure = PressureConfig(ratio=0.01, model_context_tokens=100, chars_per_token=1)
    msgs = [{"role": "user", "content": "x" * 50} for _ in range(30)]
    result = await compress(msgs, aux=None, protect_last_n=5, pressure=pressure)
    assert result.compressed
    assert result.used_trim_fallback


def test_sqlite_registry(tmp_path: Path) -> None:
    settings = LatticeSettings(
        home=tmp_path,
        sqlite=SqliteConfig(
            databases={"notes": SqliteDatabaseConfig(path=str(tmp_path / "notes.db"))}
        ),
    )
    reg = SqliteRegistry(settings)
    assert reg.list()[0].name == "notes"
    reg.register("metrics", tmp_path / "metrics.db")
    with pytest.raises(ValueError):
        reg.register("state", tmp_path / "x.db")


def test_ensure_default_profile(tmp_path: Path) -> None:
    ensure_default_profile(tmp_path)
    p = load_profile("default", tmp_path)
    assert p.soul
