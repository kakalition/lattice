"""Agent tool: browser_snapshot."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.browser import browser_snapshot


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def browser_snapshot_tool(
        ctx: RunContext[TurnDeps],
        mode: str = "a11y",
        max_chars: int = 30_000,
    ) -> str:
        """Inspect the current Puppeteer/Chromium page before interacting.

        mode=a11y → accessibility / ARIA tree (best for finding click targets).
        mode=text → visible text dump of the body.
        """
        if err := not_allowed(ctx, "browser_snapshot"):
            return err
        return await traced(
            ctx,
            "browser_snapshot",
            {"mode": mode, "max_chars": max_chars},
            lambda: browser_snapshot(mode=mode, max_chars=max_chars),
        )

    return {"browser_snapshot": browser_snapshot_tool}
