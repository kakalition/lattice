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
    ):
        (home / sub).mkdir(parents=True, exist_ok=True)
    return home
