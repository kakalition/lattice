"""Agent tool: browser_snapshot."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolTier, ToolsetT
from lattice.tools.browser import browser_snapshot as _browser_snapshot

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def browser_snapshot(
        ctx: RunContext[TurnDeps],
        mode: str = "a11y",
        max_chars: int = 30_000,
    ) -> str:
        """Inspect the current Puppeteer/Chromium page before interacting.

        mode=a11y → accessibility / ARIA tree (best for finding click targets).
        mode=text → visible text dump of the body.
        """
        return await traced(
            ctx,
            "browser_snapshot",
            {"mode": mode, "max_chars": max_chars},
            lambda: _browser_snapshot(mode=mode, max_chars=max_chars),
        )

    return {"browser_snapshot": browser_snapshot}
