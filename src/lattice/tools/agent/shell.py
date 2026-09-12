"""Agent tool: shell."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.audit import audit_log
from lattice.deps import TurnDeps, maybe_approve, traced, truncate_result
from lattice.tools.agent._common import ToolsetT, ToolTier
from lattice.tools.shell import DEFAULT_TIMEOUT_S, run_shell

TIER = ToolTier.EAGER


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def shell(
        ctx: RunContext[TurnDeps], command: str, timeout: float = DEFAULT_TIMEOUT_S
    ) -> str:
        denied = await maybe_approve(ctx, "shell", command, command=command)
        if denied:
            return denied

        async def _op() -> str:
            try:
                result = await run_shell(command, timeout=timeout)
                out = truncate_result(f"exit={result.exit_code}\n{result.stdout}\n{result.stderr}")
            except Exception as exc:
                out = f"shell error: {exc}"
            audit_log("tool", {"name": "shell", "command": command}, home=ctx.deps.settings.home)
            return out

        return await traced(ctx, "shell", {"command": command, "timeout": timeout}, _op)

    return {"shell": shell}
