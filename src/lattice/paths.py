"""Paths and home directory helpers for <project>/.lattice."""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Repo root (directory with pyproject.toml), else cwd."""
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


def lattice_home() -> Path:
    """Runtime data root: $LATTICE_HOME or <project>/.lattice (state, logs, memory)."""
    env = os.environ.get("LATTICE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return project_root() / ".lattice"


def user_config_path(home: Path | None = None) -> Path:
    """Operator-controlled settings (models, timezone, tools) — not under a hidden data dir.

    - Default install: ``<project>/lattice.yaml`` (visible, editable)
    - ``LATTICE_HOME`` or isolated ``home=`` (tests): ``<home>/lattice.yaml``
    """
    if os.environ.get("LATTICE_HOME"):
        return lattice_home() / "lattice.yaml"
    if home is not None:
        h = home.resolve()
        if h != (project_root() / ".lattice").resolve():
            return h / "lattice.yaml"
    return project_root() / "lattice.yaml"


# Chromium executable path relative to an installed ``ms-playwright/chromium-<rev>`` dir.
_CHROMIUM_EXE_SUFFIXES = {
    "darwin": (
        "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    ),
    "linux": ("chrome-linux/chrome",),
    "win32": ("chrome-win/chrome.exe",),
}


def playwright_browsers_dir() -> Path:
    """Root Playwright installs browsers under (honours ``PLAYWRIGHT_BROWSERS_PATH``)."""
    import os

    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Caches" / "ms-playwright"  # macOS default


def chromium_executable() -> Path | None:
    """Locate the installed Chromium binary without launching the Playwright driver.

    ``sync_playwright().chromium.executable_path`` answers the same question but
    starts a driver subprocess, which leaves a pending connection task that
    Playwright reports as ``Task was destroyed but it is pending!`` /
    ``TargetClosedError`` on interpreter shutdown. This is called from boot-time
    ``oneshot`` checks, so it must stay silent.
    """
    import glob
    import os
    import sys

    suffixes = _CHROMIUM_EXE_SUFFIXES.get(sys.platform or "")
    if not suffixes:
        return None
    roots: list[Path] = []
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        roots.append(Path(override).expanduser())
    else:
        home = Path.home()
        roots.extend(
            [
                home / "Library" / "Caches" / "ms-playwright",  # macOS
                home / ".cache" / "ms-playwright",  # Linux
                Path(os.environ.get("LOCALAPPDATA", home)) / "ms-playwright",  # Windows
            ]
        )
    for root in roots:
        # Newest revision first: a stale leftover install must not win.
        for install in sorted(glob.glob(str(root / "chromium-*")), reverse=True):
            for suffix in suffixes:
                candidate = Path(install) / suffix
                if candidate.is_file():
                    return candidate
    return None


def ensure_home() -> Path:
    home = lattice_home()
    for sub in (
        "profiles/default",
        "skills",
        "qdrant",
        "scheduler",
        "sqlite/backups",
        "workspace",
        "logs",
        "browser/profile",
        "metrics",
        "scripts",
    ):
        (home / sub).mkdir(parents=True, exist_ok=True)
    return home
