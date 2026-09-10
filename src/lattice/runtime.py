"""Per-turn runtime context (cwd etc.)."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

cwd_var: ContextVar[Path | None] = ContextVar("lattice_cwd", default=None)


def get_cwd() -> Path:
    return cwd_var.get() or Path.cwd()


def set_cwd(path: Path):
    return cwd_var.set(path.resolve())
