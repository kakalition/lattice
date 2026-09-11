"""One-time setup registry tests."""

from __future__ import annotations

from pathlib import Path

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


def test_daily_briefing_in_starters() -> None:
    from lattice.setup import SKILL_STARTERS

    assert "daily-briefing" in SKILL_STARTERS
