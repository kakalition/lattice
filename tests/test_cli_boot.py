"""Boot-time self-checks must abort loudly rather than degrade silently."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

from lattice.cli import _boot_memory_self_check


def _settings(*, self_check: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        memory=SimpleNamespace(self_check=self_check),
        home=Path("/tmp/lattice-boot-test"),
    )


def test_memory_self_check_exits_on_failure(monkeypatch) -> None:
    """A broken memory round-trip must stop boot instead of running degraded.

    Memory search once failed silently for every turn; the agent kept answering
    with no recall and nothing surfaced. A failed probe is now fatal.
    """
    import lattice.agent_app as agent_app
    import lattice.profiles as profiles

    monkeypatch.setattr(agent_app, "verify_memory_for_profile", _raise_runtime)
    monkeypatch.setattr(profiles, "get_profile", lambda *a, **k: SimpleNamespace(id="default"))

    with pytest.raises(typer.Exit) as exc:
        _boot_memory_self_check(_settings(), "default")
    assert exc.value.exit_code == 1


def test_memory_self_check_is_skippable(monkeypatch) -> None:
    """`memory.self_check: false` must let boot proceed without probing."""
    import lattice.agent_app as agent_app
    import lattice.profiles as profiles

    def _explode(*a, **k):
        raise AssertionError("probe must not run when disabled")

    monkeypatch.setattr(agent_app, "verify_memory_for_profile", _explode)
    monkeypatch.setattr(profiles, "get_profile", _explode)

    _boot_memory_self_check(_settings(self_check=False), "default")


def test_memory_self_check_passes_notes_through(monkeypatch) -> None:
    """On success the check returns normally and does not print an error."""
    import lattice.agent_app as agent_app
    import lattice.profiles as profiles

    monkeypatch.setattr(
        agent_app, "verify_memory_for_profile", lambda *a, **k: ["memory self-check: ok"]
    )
    monkeypatch.setattr(profiles, "get_profile", lambda *a, **k: SimpleNamespace(id="default"))

    _boot_memory_self_check(_settings(), "default")


def _raise_runtime(*a, **k):
    raise RuntimeError("memory self-check FAILED: search did not return the probe")


def test_init_reset_archives_and_reinitializes(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from lattice.cli import app

    runner = CliRunner()
    home = tmp_path / "freshhome"
    first = runner.invoke(app, ["init", "--home", str(home)])
    assert first.exit_code == 0, first.output

    marker = home / "workspace" / "keep.txt"
    marker.write_text("old", encoding="utf-8")
    (home / "state.db").write_text("db", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--reset", "--home", str(home)])
    assert result.exit_code == 0, result.output
    archives = list(tmp_path.glob("freshhome-*.tar.gz"))
    assert archives, result.output
    assert not marker.exists(), "old home content survived --reset"
    assert (home / "lattice.yaml").is_file(), "home was not re-initialized"


def test_init_reset_refuses_running_gateway(tmp_path: Path) -> None:
    import os

    from typer.testing import CliRunner

    from lattice.cli import app

    home = tmp_path / "gwhome"
    home.mkdir(parents=True)
    (home / "gateway.pid").write_text(str(os.getpid()), encoding="utf-8")

    result = CliRunner().invoke(app, ["init", "--reset", "--home", str(home)])
    assert result.exit_code == 1
    assert "gateway is running" in result.output
