"""Agent tool: remove_path."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, maybe_approve, traced
from lattice.tools.agent._common import ToolsetT, ToolTier
from lattice.tools.files import remove_path as _remove_path
from lattice.tools.files import validate_removal

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def remove_path(
        ctx: RunContext[TurnDeps],
        path: str,
        recursive: bool = False,
        missing_ok: bool = False,
    ) -> str:
        """Remove a file, symlink, or directory under the workspace.

        Deleting a non-empty directory requires recursive=True. Removal is
        approval-gated; symlinks are removed as links and never followed.
        """
        # Validate first: never prompt for an operation that cannot run.
        error = validate_removal(
            path,
            workspace=ctx.deps.workspace,
            home=ctx.deps.settings.home,
            recursive=recursive,
            missing_ok=missing_ok,
        )
        if error is not None:
            return error
        denied = await maybe_approve(
            ctx,
            "remove_path",
            path,
            path=path,
            recursive=recursive,
            missing_ok=missing_ok,
        )
        if denied:
            return denied
        return await traced(
            ctx,
            "remove_path",
            {"path": path, "recursive": recursive},
            lambda: _remove_path(
                path,
                workspace=ctx.deps.workspace,
                home=ctx.deps.settings.home,
                recursive=recursive,
                missing_ok=missing_ok,
            ),
        )

    return {"remove_path": remove_path}
