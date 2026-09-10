"""Paths and home directory helpers for ~/.lattice."""

from __future__ import annotations

from pathlib import Path


def lattice_home() -> Path:
    return Path.home() / ".lattice"


def ensure_home() -> Path:
    home = lattice_home()
    for sub in (
        "profiles/default",
        "skills",
        "chroma",
        "scheduler",
        "sqlite/backups",
    ):
        (home / sub).mkdir(parents=True, exist_ok=True)
    return home
