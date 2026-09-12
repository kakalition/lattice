"""Root-wide shell scans are rejected before running; normal commands work."""

from __future__ import annotations

import asyncio

import pytest

from lattice.hitl.policies import unbounded_scan_reason
from lattice.tools.shell import DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S, run_shell


@pytest.mark.parametrize(
    "command",
    [
        "find / -name finance.db",
        "find ~ -name '*.png'",
        "du -sh /",
        "grep -r needle /",
        "ls -R /",
        "cat x && find $HOME -name y",
    ],
)
def test_root_scans_are_rejected(command: str) -> None:
    assert unbounded_scan_reason(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "find . -name finance.db",
        "find ./workspace -name chart.png",
        "ls -la",
        "grep -rn needle src",
        "ls -R ./subdir",
    ],
)
def test_narrow_commands_are_allowed(command: str) -> None:
    assert unbounded_scan_reason(command) is None


def test_run_shell_refuses_root_scan() -> None:
    with pytest.raises(ValueError, match="unbounded filesystem scan"):
        asyncio.run(run_shell("find / -name nope"))


def test_defaults_are_tighter() -> None:
    assert DEFAULT_TIMEOUT_S == 30.0
    assert MAX_TIMEOUT_S == 120.0


def test_normal_command_runs() -> None:
    result = asyncio.run(run_shell("echo hi"))
    assert result.exit_code == 0
    assert "hi" in result.stdout
