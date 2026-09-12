"""Agent tool: browser_interact."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import ToolTier, ToolsetT
from lattice.tools.browser import browser_interact as _browser_interact

TIER = ToolTier.COLD


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def browser_interact(
        ctx: RunContext[TurnDeps],
        action: str,
        selector: str | None = None,
        value: str | None = None,
        url: str | None = None,
        timeout_ms: int = 15_000,
    ) -> str:
        """Drive a headless Chromium page (SPA/forms/portals).

        Actions: navigate (needs url), click/type/select (needs selector; type/select need value),
        extract_text (optional selector, default body), close.
        Prefer browser_snapshot before click/type to discover selectors.
        Use web_fetch for static HTML instead.
        """
        return await traced(
            ctx,
            "browser_interact",
            {
                "action": action,
                "selector": selector,
                "url": url,
                "timeout_ms": timeout_ms,
            },
            lambda: _browser_interact(
                action,
                selector=selector,
                value=value,
                url=url,
                timeout_ms=timeout_ms,
            ),
        )

    return {"browser_interact": browser_interact}
