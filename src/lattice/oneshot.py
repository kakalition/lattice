"""One-time setup steps for ``lattice chat`` / ``lattice gateway``.

Register expensive or external installs here. Lattice persists completion under
``.lattice/setup-once.json`` so each step runs at most once per ``id``+``version``
(unless the satisfaction check fails — then it re-runs).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from lattice.paths import lattice_home

logger = logging.getLogger("lattice.oneshot")

STATE_NAME = "setup-once.json"


class OneshotStep(BaseModel):
    """A boot-time step that should run once (until version bumps or check fails)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    description: str
    version: int
    is_satisfied: Callable[[], bool]
    run: Callable[[], None]
    # If False, missing package / unsupported env → skip without failing boot.
    required: bool = False


def _state_path(home: Path | None = None) -> Path:
    return (home or lattice_home()) / STATE_NAME


def load_oneshot_state(home: Path | None = None) -> dict[str, Any]:
    path = _state_path(home)
    if not path.is_file():
        return {"completed": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"completed": {}}
    if not isinstance(data, dict):
        return {"completed": {}}
    completed = data.get("completed")
    if not isinstance(completed, dict):
        data["completed"] = {}
    return data


def save_oneshot_state(state: dict[str, Any], home: Path | None = None) -> Path:
    root = home or lattice_home()
    root.mkdir(parents=True, exist_ok=True)
    path = _state_path(root)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _mark_completed(state: dict[str, Any], step: OneshotStep, *, detail: str = "") -> None:
    completed = state.setdefault("completed", {})
    completed[step.id] = {
        "version": step.version,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "detail": detail,
    }


def _already_recorded(state: dict[str, Any], step: OneshotStep) -> bool:
    entry = (state.get("completed") or {}).get(step.id)
    if not isinstance(entry, dict):
        return False
    return int(entry.get("version") or 0) >= step.version


def _chromium_executable() -> Path | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path)
    except Exception:
        return None


def playwright_chromium_satisfied() -> bool:
    exe = _chromium_executable()
    return bool(exe and exe.is_file())


def install_playwright_chromium() -> None:
    exe_before = _chromium_executable()
    if exe_before and exe_before.is_file():
        return
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    logger.info("oneshot running: %s", " ".join(cmd))
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-800:]
        raise RuntimeError(
            f"playwright install chromium failed (exit {result.returncode}): {tail or 'no output'}"
        )
    if not playwright_chromium_satisfied():
        raise RuntimeError(
            "playwright install chromium finished but Chromium executable still missing"
        )


# Registry — add new one-time boot steps here. Bump ``version`` to force a re-run.
ONESHOT_STEPS: tuple[OneshotStep, ...] = (
    OneshotStep(
        id="playwright-chromium",
        description="Install Playwright Chromium for browser_interact / browser_snapshot",
        version=1,
        is_satisfied=playwright_chromium_satisfied,
        run=install_playwright_chromium,
        required=False,
    ),
)


def oneshot_status(home: Path | None = None) -> list[str]:
    """Human lines for ``lattice doctor``."""
    state = load_oneshot_state(home)
    lines: list[str] = []
    for step in ONESHOT_STEPS:
        ok = step.is_satisfied()
        recorded = _already_recorded(state, step)
        if ok and recorded:
            status = "done"
        elif ok:
            status = "satisfied (will record on next chat/gateway)"
        elif recorded:
            status = "recorded but missing — will re-run on next chat/gateway"
        else:
            status = "pending"
        lines.append(f"oneshot/{step.id}: {status} (v{step.version})")
    return lines


def ensure_oneshot_setup(
    home: Path | None = None,
    *,
    console: Any | None = None,
    steps: tuple[OneshotStep, ...] | None = None,
) -> list[str]:
    """Run pending one-time steps. Returns log lines of what happened."""
    root = home or lattice_home()
    root.mkdir(parents=True, exist_ok=True)
    state = load_oneshot_state(root)
    registry = steps if steps is not None else ONESHOT_STEPS
    notes: list[str] = []

    def _emit(msg: str) -> None:
        notes.append(msg)
        logger.info("%s", msg)
        if console is not None:
            console.print(f"[dim]{msg}[/]")

    for step in registry:
        if step.is_satisfied():
            if not _already_recorded(state, step):
                _mark_completed(state, step, detail="already satisfied")
                save_oneshot_state(state, root)
                _emit(f"oneshot {step.id}: recorded (already present)")
            continue

        _emit(f"oneshot {step.id}: {step.description}…")
        try:
            step.run()
        except Exception as exc:
            msg = f"oneshot {step.id}: failed — {exc}"
            notes.append(msg)
            logger.warning("%s", msg)
            if console is not None:
                style = "red" if step.required else "yellow"
                console.print(f"[{style}]{msg}[/]")
            if step.required:
                raise
            continue

        if not step.is_satisfied():
            msg = f"oneshot {step.id}: ran but still not satisfied"
            notes.append(msg)
            logger.warning("%s", msg)
            if console is not None:
                console.print(f"[yellow]{msg}[/]")
            if step.required:
                raise RuntimeError(msg)
            continue

        _mark_completed(state, step, detail="installed")
        save_oneshot_state(state, root)
        _emit(f"oneshot {step.id}: done")

    return notes
