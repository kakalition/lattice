"""Built-in ``files`` group — workspace filesystem access."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai import RunContext

from lattice.audit import audit_log
from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.tools import read_cache
from lattice.tools.file import (
    edit_file as _edit_file,
)
from lattice.tools.file import (
    read_file as _read_file,
)
from lattice.tools.file import (
    search_files as _search_files,
)
from lattice.tools.file import (
    write_file as _write_file,
)
from lattice.tools.file_safety import resolve_agent_path
from lattice.tools.files import remove_path as _remove_path
from lattice.tools.files import validate_removal
from lattice.tools.groups._common import ToolBinding, ToolGroup, ToolsetT
from lattice.tools.middleware import ToolPolicy
from lattice.tools.shell import DEFAULT_TIMEOUT_S, run_shell

_GROUP = "files"

_SHELL_POLICY = ToolPolicy(summary=lambda _ctx, args: str(args.get("command") or ""))


def _remove_precheck(ctx: RunContext[TurnDeps], args: dict[str, Any]) -> str | None:
    return validate_removal(
        str(args.get("path") or ""),
        workspace=ctx.deps.workspace,
        home=ctx.deps.settings.home,
        recursive=bool(args.get("recursive")),
        missing_ok=bool(args.get("missing_ok")),
    )


_REMOVE_POLICY = ToolPolicy(
    precheck=_remove_precheck,
    summary=lambda _ctx, args: str(args.get("path") or ""),
)


def register_shell(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def shell(
        ctx: RunContext[TurnDeps], command: str, timeout: float = DEFAULT_TIMEOUT_S
    ) -> str:
        try:
            result = await run_shell(command, timeout=timeout)
            # `traced` owns truncation (head+tail with a scratch reference), so
            # stderr at the tail survives instead of being pre-sliced away.
            out = f"exit={result.exit_code}\n{result.stdout}\n{result.stderr}"
        except Exception as exc:
            out = f"shell error: {exc}"
        audit_log(
            "tool",
            {"name": f"{_GROUP}/shell", "command": command},
            home=ctx.deps.settings.home,
        )
        return out

    return {"shell": shell}


def register_read(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def read(ctx: RunContext[TurnDeps], path: str, offset: int = 0, limit: int = 0) -> str:
        """Read a file; optional ``offset``/``limit`` select a line range.

        ``offset`` is a 0-based line index and ``limit`` a line count (0 = to the
        end). With neither set, the whole (bounded) file is returned.
        """
        home = ctx.deps.settings.home
        # Scope by turn: tool results are not replayed, so a body served on a
        # previous turn is no longer in context and must be re-served.
        scope = ctx.deps.session_id
        if ctx.deps.turn_id:
            scope = f"{scope}:{ctx.deps.turn_id}"
        ranged = bool(offset or limit)
        target = resolve_agent_path(path, ctx.deps.workspace, home=home)
        # The cache identity must include the range, or a ranged read would
        # elide a later full read of the same unchanged file.
        cache_path = str(target) if not ranged else f"{target}#offset={offset}&limit={limit}"
        stat = await asyncio.to_thread(target.stat)
        if read_cache.is_unchanged(scope, cache_path, stat.st_mtime_ns, stat.st_size):
            label = path if not ranged else f"{path} (offset={offset}, limit={limit})"
            return f"[unchanged since last read: {label} ({stat.st_size} bytes)]"
        text = await _read_file(
            path, workspace=ctx.deps.workspace, home=home, offset=offset, limit=limit
        )
        fresh = await asyncio.to_thread(target.stat)
        read_cache.record_read(scope, cache_path, fresh.st_mtime_ns, fresh.st_size)
        return text

    return {"read": read}


def register_write(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def write(ctx: RunContext[TurnDeps], path: str, content: str) -> str:
        return await _write_file(
            path, content, workspace=ctx.deps.workspace, home=ctx.deps.settings.home
        )

    return {"write": write}


def register_edit(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def edit(ctx: RunContext[TurnDeps], path: str, old_string: str, new_string: str) -> str:
        return await _edit_file(
            path,
            old_string,
            new_string,
            workspace=ctx.deps.workspace,
            home=ctx.deps.settings.home,
        )

    return {"edit": edit}


def register_remove(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def remove(
        ctx: RunContext[TurnDeps],
        path: str,
        recursive: bool = False,
        missing_ok: bool = False,
    ) -> str:
        """Remove a file, symlink, or directory under the workspace.

        Deleting a non-empty directory requires recursive=True. Removal is
        approval-gated; symlinks are removed as links and never followed.
        """
        return await _remove_path(
            path,
            workspace=ctx.deps.workspace,
            home=ctx.deps.settings.home,
            recursive=recursive,
            missing_ok=missing_ok,
        )

    return {"remove": remove}


def register_search(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def search(ctx: RunContext[TurnDeps], pattern: str, glob: str = "**/*") -> str:
        """Find a literal substring in workspace files (not a regex).

        Returns ``path:line: text`` for content matches; ``glob`` optionally
        narrows the scanned files (default ``**/*``).
        """
        return await _search_files(pattern, workspace=ctx.deps.workspace, glob=glob)

    return {"search": search}


GROUP = ToolGroup(
    name=_GROUP,
    description="Workspace filesystem: shell, read, write, edit, remove, search.",
    bindings=(
        ToolBinding("shell", ToolTier.EAGER, register_shell, _SHELL_POLICY),
        ToolBinding("read", ToolTier.EAGER, register_read),
        ToolBinding("write", ToolTier.EAGER, register_write),
        ToolBinding("edit", ToolTier.EAGER, register_edit),
        ToolBinding("remove", ToolTier.EAGER, register_remove, _REMOVE_POLICY),
        ToolBinding("search", ToolTier.EAGER, register_search),
    ),
)
