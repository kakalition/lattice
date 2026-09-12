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
