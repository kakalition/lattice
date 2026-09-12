"""Tests for execute_script (bwrap/soft sandbox)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice.config import ScriptsConfig
from lattice.deps import CORE_TOOL_NAMES
from lattice.hitl.policies import tool_needs_approval
from lattice.tools.script import (
    build_bwrap_command,
    build_soft_command,
    bwrap_available,
    execute_script,
    format_script_result,
)


def test_core_tools_include_execute_script() -> None:
    assert "execute_script" in CORE_TOOL_NAMES


def test_build_toolsets_includes_new_tools() -> None:
    from lattice.tools.agent import tool_functions

    mapping = tool_functions()
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


def test_build_bwrap_command_binds_skill_and_tool_trees(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "skills").mkdir(parents=True)
    (home / "tools").mkdir(parents=True)
    common = {
        "interpreter": "/usr/bin/python3",
        "script_path": tmp_path / "x.py",
        "workspace": tmp_path,
        "scripts_root": home / "scripts",
        "allow_network": False,
    }
    cmds = [
        build_bwrap_command(**common, extra_ro_binds=[home / "skills", home / "tools"]),
        build_bwrap_command(**common, home=home),
    ]
    for cmd in cmds:
        for tree in (home / "skills", home / "tools"):
            resolved = str(tree.resolve())
            assert resolved in cmd
            # Read-only: never exposed writable via --bind.
            idx = cmd.index(resolved)
            assert cmd[idx - 1] == "--ro-bind"
            assert ["--bind", resolved, resolved] != cmd[idx - 1 : idx + 2]


@pytest.mark.asyncio
async def test_execute_script_passes_stdin_and_env(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    code = (
        "import json, os, sys\n"
        "print('stdin', sys.stdin.read().strip())\n"
        "print('env', os.environ.get('LATTICE_TEST_ENV', ''))\n"
    )
    result = await execute_script(
        language="python",
        code=code,
        workspace=workspace,
        home=home,
        cfg=ScriptsConfig(),
        timeout=15,
        stdin='{"a": 1}',
        env_extra={"LATTICE_TEST_ENV": "from-env"},
    )
    assert result.exit_code == 0
    assert 'stdin {"a": 1}' in result.stdout
    assert "env from-env" in result.stdout


@pytest.mark.asyncio
async def test_execute_script_sets_lattice_home(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    code = "import os\nprint('home', os.environ.get('LATTICE_HOME', ''))\n"
    result = await execute_script(
        language="python",
        code=code,
        workspace=workspace,
        home=home,
        cfg=ScriptsConfig(),
        timeout=15,
    )
    assert result.exit_code == 0
    assert f"home {home.resolve()}" in result.stdout


def test_new_skill_starters_present() -> None:
    from lattice.setup import SKILL_STARTERS

    for name in (
        "task-decomposer",
        "tool-authoring",
        "script-authoring",
        "data-pipeline",
        "daily-briefing",
        "scheduling",
        "reminder",
    ):
        assert name in SKILL_STARTERS


def test_bwrap_probe_does_not_raise() -> None:
    assert isinstance(bwrap_available(), bool)
