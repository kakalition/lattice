"""General filesystem removal (file, symlink, or directory)."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from lattice.tools.file_safety import PathDeniedError, is_denied_path, resolve_agent_path

# Never removable, regardless of jail membership: filesystem roots and the
# agent's own runtime state. ``is_denied_path`` only covers secrets.
DENY_EXACT_NAMES = frozenset({"state.db"})
DENY_UNDER_HOME = frozenset({"qdrant"})


class PathRemovalDenied(PermissionError):
    """Raised when a path is jail-legal but must not be deleted."""


def _assert_removable(target: Path, *, workspace: Path, home: Path | None) -> None:
    if target == target.parent:
        raise PathRemovalDenied(f"refusing to remove filesystem root: {target}")
    if target.name.lower() in DENY_EXACT_NAMES:
        raise PathRemovalDenied(f"refusing to remove protected file: {target}")
    if target == workspace.expanduser().resolve():
        raise PathRemovalDenied(f"refusing to remove the workspace root: {target}")
    if home is not None:
        home_resolved = home.expanduser().resolve()
        if target == home_resolved:
            raise PathRemovalDenied(f"refusing to remove lattice home: {target}")
        for name in DENY_UNDER_HOME:
            if target == (home_resolved / name).resolve():
                raise PathRemovalDenied(f"refusing to remove protected path: {target}")


def _resolve_for_removal(path: str, *, workspace: Path, home: Path | None) -> Path:
    """Resolve the *entry* to delete without following a final symlink.

    ``resolve_agent_path`` runs ``Path.resolve()``, which follows symlinks. For
    deletion that is wrong twice over: it would delete a link's target rather
    than the link, and a link pointing outside the jail would escape as the
    target. So the lexical parent is jail-checked via the normal resolver, and
    the final component is taken unresolved.
    """
    lexical = Path(path).expanduser()
    parent, name = lexical.parent, lexical.name
    if name in {"", ".", ".."}:
        # Parent-specific rules reject these; resolve so the caller sees the root.
        return resolve_agent_path(path, workspace, home=home or workspace)
    base = Path(".") if str(parent) == "." else parent
    parent_resolved = resolve_agent_path(base, workspace, home=home or workspace)
    candidate = parent_resolved / name
    if is_denied_path(candidate):
        raise PathRemovalDenied(f"path denied: {candidate}")
    return candidate


def validate_removal(
    path: str,
    *,
    workspace: Path,
    home: Path | None = None,
    recursive: bool = False,
    missing_ok: bool = False,
) -> str | None:
    """Return a user-facing error if the removal cannot proceed, else ``None``.

    Runs the jail / protected-path checks before HITL so an out-of-jail or
    otherwise invalid target never triggers a futile approval prompt.
    """
    try:
        target = _resolve_for_removal(path, workspace=workspace, home=home)
        _assert_removable(target, workspace=workspace, home=home)
    except (PathDeniedError, PathRemovalDenied, OSError) as exc:
        return f"error: {exc}"
    try:
        os.lstat(target)
    except FileNotFoundError:
        return None if missing_ok else f"error: nothing to remove: {target}"
    except OSError as exc:
        return f"error: {exc}"
    if not os.path.islink(target) and os.path.isdir(target) and not recursive:
        try:
            with os.scandir(target) as entries:
                if any(True for _ in entries):
                    return f"error: directory not empty: {target} (pass recursive=True to remove)"
        except OSError as exc:
            return f"error: {exc}"
    return None


def _count_entries(target: Path) -> int:
    total = 0
    for _root, dirs, files in os.walk(target, followlinks=False):
        total += len(dirs) + len(files)
    return total


async def remove_path(
    path: str,
    *,
    workspace: Path,
    home: Path | None = None,
    recursive: bool = False,
    missing_ok: bool = False,
) -> str:
    """Remove a file, symlink, or directory inside the workspace / lattice home jail.

    A non-empty directory requires ``recursive=True``. Symlinks are removed as
    links (never followed), so a link pointing outside the jail cannot be used
    to delete its target.
    """
    target = _resolve_for_removal(path, workspace=workspace, home=home)
    _assert_removable(target, workspace=workspace, home=home)

    def _remove() -> str:
        # lstat: inspect the link itself, never its target.
        try:
            os.lstat(target)
        except FileNotFoundError:
            if missing_ok:
                return f"nothing to remove: {target}"
            raise

        if os.path.islink(target) or not os.path.isdir(target):
            os.unlink(target)
            return f"removed file {target}"
        try:
            os.rmdir(target)
        except OSError as exc:
            if not recursive:
                raise ValueError(
                    f"directory not empty: {target} (pass recursive=True to remove)"
                ) from exc
            count = _count_entries(target)
            shutil.rmtree(target)
            return f"removed directory {target} ({count} entries)"
        return f"removed directory {target}"

    return await asyncio.to_thread(_remove)
