"""One-time setup registry tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lattice.oneshot import (
    ONESHOT_STEPS,
    OneshotStep,
    ensure_oneshot_setup,
    load_oneshot_state,
    oneshot_status,
)


def test_registry_includes_playwright_chromium() -> None:
    ids = {s.id for s in ONESHOT_STEPS}
    assert "playwright-chromium" in ids


def test_ensure_oneshot_runs_once(tmp_path: Path) -> None:
    calls = {"n": 0}
    flag = tmp_path / "ready"

    def satisfied() -> bool:
        return flag.exists()

    def run() -> None:
        calls["n"] += 1
        flag.write_text("1", encoding="utf-8")

    step = OneshotStep(
        id="demo-step",
        description="demo",
        version=1,
        is_satisfied=satisfied,
        run=run,
    )
    notes1 = ensure_oneshot_setup(tmp_path, steps=(step,))
    assert calls["n"] == 1
    assert any("done" in n for n in notes1)
    state = load_oneshot_state(tmp_path)
    assert state["completed"]["demo-step"]["version"] == 1

    notes2 = ensure_oneshot_setup(tmp_path, steps=(step,))
    assert calls["n"] == 1
    assert notes2 == [] or all("demo-step" not in n or "done" not in n for n in notes2)


def test_ensure_oneshot_reruns_when_missing_after_record(tmp_path: Path) -> None:
    calls = {"n": 0}
    flag = tmp_path / "ready"

    def satisfied() -> bool:
        return flag.exists()

    def run() -> None:
        calls["n"] += 1
        flag.write_text("1", encoding="utf-8")

    step = OneshotStep(
        id="flaky",
        description="flaky",
        version=1,
        is_satisfied=satisfied,
        run=run,
    )
    ensure_oneshot_setup(tmp_path, steps=(step,))
    assert calls["n"] == 1
    flag.unlink()
    ensure_oneshot_setup(tmp_path, steps=(step,))
    assert calls["n"] == 2


def test_ensure_oneshot_version_bump_reruns(tmp_path: Path) -> None:
    calls = {"n": 0}
    flag = tmp_path / "ready"
    flag.write_text("1", encoding="utf-8")

    def satisfied() -> bool:
        return flag.exists()

    def run_noop() -> None:
        calls["n"] += 1

    v1 = OneshotStep(id="bump", description="v1", version=1, is_satisfied=satisfied, run=run_noop)
    ensure_oneshot_setup(tmp_path, steps=(v1,))
    # Already satisfied → recorded without run
    assert calls["n"] == 0
    assert load_oneshot_state(tmp_path)["completed"]["bump"]["version"] == 1

    flag.unlink()

    def run2() -> None:
        calls["n"] += 1
        flag.write_text("1", encoding="utf-8")

    v2 = OneshotStep(id="bump", description="v2", version=2, is_satisfied=satisfied, run=run2)
    ensure_oneshot_setup(tmp_path, steps=(v2,))
    assert calls["n"] == 1
    assert load_oneshot_state(tmp_path)["completed"]["bump"]["version"] == 2


def test_ensure_oneshot_non_required_failure_does_not_raise(tmp_path: Path) -> None:
    def satisfied() -> bool:
        return False

    def run() -> None:
        raise RuntimeError("boom")

    step = OneshotStep(
        id="soft",
        description="soft fail",
        version=1,
        is_satisfied=satisfied,
        run=run,
        required=False,
    )
    notes = ensure_oneshot_setup(tmp_path, steps=(step,))
    assert any("failed" in n for n in notes)
    assert "soft" not in load_oneshot_state(tmp_path).get("completed", {})


def test_oneshot_status_lines(tmp_path: Path) -> None:
    lines = oneshot_status(tmp_path)
    assert any(line.startswith("oneshot/playwright-chromium:") for line in lines)


def test_chromium_executable_found_without_playwright_driver(tmp_path: Path, monkeypatch) -> None:
    """The lookup must locate an installed Chromium purely from the filesystem.

    Regression: resolving the path via ``sync_playwright().chromium.executable_path``
    starts a driver that leaves a pending connection task, which Playwright then
    reports as ``Task was destroyed but it is pending!`` on shutdown. This runs on
    every gateway boot, so it has to stay silent.
    """
    import sys

    from lattice.paths import _CHROMIUM_EXE_SUFFIXES, chromium_executable

    suffixes = _CHROMIUM_EXE_SUFFIXES.get(sys.platform or "")
    if not suffixes:  # pragma: no cover - unsupported platform
        pytest.skip(f"no chromium suffix for platform {sys.platform!r}")

    install = tmp_path / "chromium-9999"
    exe = install / suffixes[0]
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    assert chromium_executable() == exe


def test_chromium_executable_prefers_newest_revision(tmp_path: Path, monkeypatch) -> None:
    import sys

    from lattice.paths import _CHROMIUM_EXE_SUFFIXES, chromium_executable

    suffixes = _CHROMIUM_EXE_SUFFIXES.get(sys.platform or "")
    if not suffixes:  # pragma: no cover - unsupported platform
        pytest.skip(f"no chromium suffix for platform {sys.platform!r}")

    for rev in ("100", "111"):
        exe = tmp_path / f"chromium-{rev}" / suffixes[0]
        exe.parent.mkdir(parents=True)
        exe.write_text("x", encoding="utf-8")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    found = chromium_executable()
    assert found is not None
    assert "chromium-111" in str(found), "stale revision won over the newest install"


def test_chromium_executable_none_when_absent(tmp_path: Path, monkeypatch) -> None:
    from lattice.paths import chromium_executable

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty"))
    assert chromium_executable() is None


def test_oneshot_chromium_probe_is_quiet(tmp_path: Path) -> None:
    """The boot-time probe must not emit Playwright shutdown noise."""
    import subprocess

    from lattice.paths import chromium_executable

    assert chromium_executable() is not None, "expected Chromium installed for this check"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from lattice.oneshot import _chromium_executable; print(_chromium_executable())",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Task was destroyed" not in proc.stderr
    assert "TargetClosedError" not in proc.stderr


def test_daily_briefing_in_starters() -> None:
    from lattice.setup import SKILL_STARTERS

    assert "daily-briefing" in SKILL_STARTERS
