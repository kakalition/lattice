"""Agent tool: shell."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.audit import audit_log
from lattice.deps import TurnDeps, maybe_approve, traced, truncate_result
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.shell import run_shell


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def shell(ctx: RunContext[TurnDeps], command: str, timeout: float = 60.0) -> str:
        if err := not_allowed(ctx, "shell"):
            return err
        denied = await maybe_approve(ctx, "shell", command, command=command)
        if denied:
            return denied

        async def _op() -> str:
            try:
                result = await run_shell(command, timeout=timeout)
                out = truncate_result(
                    f"exit={result.exit_code}\n{result.stdout}\n{result.stderr}"
                )
            except Exception as exc:
                out = f"shell error: {exc}"
            audit_log("tool", {"name": "shell", "command": command}, home=ctx.deps.settings.home)
            return out

        return await traced(ctx, "shell", {"command": command, "timeout": timeout}, _op)

    return {"shell": shell}
