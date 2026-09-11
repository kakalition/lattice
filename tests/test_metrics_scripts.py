"""Tests for metric_log/query and execute_script (bwrap/soft)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice.config import ScriptsConfig
from lattice.deps import CORE_TOOL_NAMES
from lattice.hitl.policies import tool_needs_approval
from lattice.tools.agent import register_all
from lattice.tools.metrics import metric_log, metric_query
from lattice.tools.script import (
    build_bwrap_command,
    build_soft_command,
    bwrap_available,
    execute_script,
    format_script_result,
)


def test_core_tools_include_metrics_and_script() -> None:
    assert "metric_log" in CORE_TOOL_NAMES
    assert "metric_query" in CORE_TOOL_NAMES
    assert "execute_script" in CORE_TOOL_NAMES


def test_register_all_includes_new_tools() -> None:
    from pydantic_ai import Agent

    from lattice.deps import TurnDeps

    agent: Agent[TurnDeps, str] = Agent("test", deps_type=TurnDeps, system_prompt="x")
    mapping = register_all(agent)
    assert set(CORE_TOOL_NAMES) <= set(mapping)


def test_execute_script_hitl_only_when_dangerous() -> None:
    from lattice.hitl.policies import script_needs_approval

    assert not tool_needs_approval(
        "execute_script", args={"language": "python", "code": "print(1)"}
    )
    assert not script_needs_approval("print('ok')", language="python")
    assert tool_needs_approval(
        "execute_script",
        args={"language": "python", "code": "import subprocess; subprocess.run(['ls'])"},
    )
    assert tool_needs_approval(
        "execute_script",
        args={"language": "bash", "code": "rm -rf /tmp/x"},
    )
    assert tool_needs_approval(
        "execute_script",
        args={"language": "node", "code": "require('child_process').exec('id')"},
    )


@pytest.mark.asyncio
async def test_metric_log_and_query(tmp_path: Path) -> None:
    out = await metric_log("habit.meditation", 1, unit="bool", note="am", home=tmp_path)
    assert "logged metric" in out
    await metric_log("habit.meditation", 1, at="2026-09-10", home=tmp_path)
    await metric_log("habit.meditation", 1, at="2026-09-09", home=tmp_path)
    q = await metric_query("habit.meditation", home=tmp_path)
    assert "summary name=habit.meditation" in q
    assert "streak_" in q
    assert "habit.meditation" in q


@pytest.mark.asyncio
async def test_metric_log_rejects_bad_name(tmp_path: Path) -> None:
    out = await metric_log("!!!", 1, home=tmp_path)
    assert "error" in out


@pytest.mark.asyncio
async def test_execute_script_python_soft_or_bwrap(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    cfg = ScriptsConfig(require_bwrap=False)
    result = await execute_script(
        language="python",
        code="print('lattice-ok')",
        workspace=workspace,
        home=home,
        cfg=cfg,
        timeout=15,
    )
    assert result.exit_code == 0
    assert "lattice-ok" in result.stdout
    assert result.sandbox in {"bwrap", "soft"}
    text = format_script_result(result)
    assert "exit=0" in text


@pytest.mark.asyncio
async def test_execute_script_require_bwrap_fails_without(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lattice.tools.script.bwrap_available", lambda: False)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(RuntimeError, match="bwrap"):
        await execute_script(
            language="python",
            code="print(1)",
            workspace=workspace,
            home=tmp_path,
            cfg=ScriptsConfig(require_bwrap=True),
        )


def test_build_bwrap_command_includes_unshare_net() -> None:
    cmd = build_bwrap_command(
        interpreter="/usr/bin/python3",
        script_path=Path("/tmp/x.py"),
        workspace=Path("/tmp/ws"),
        scripts_root=Path("/tmp/scripts"),
        allow_network=False,
    )
    assert cmd[0] == "bwrap"
    assert "--unshare-net" in cmd
    soft = build_soft_command(interpreter="/usr/bin/python3", script_path=Path("/tmp/x.py"))
    assert soft[0] == "/usr/bin/python3"


def test_new_skill_starters_present() -> None:
    from lattice.setup import SKILL_STARTERS

    for name in (
        "task-decomposer",
        "evening-reflection",
        "habit-tracker",
        "goal-alignment",
        "monthly-report",
        "script-authoring",
        "data-pipeline",
        "daily-briefing",
    ):
        assert name in SKILL_STARTERS


def test_bwrap_probe_does_not_raise() -> None:
    assert isinstance(bwrap_available(), bool)
