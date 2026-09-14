"""Built-in ``browser`` group — headless Chromium interaction."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.tools.browser import browser_interact as _browser_interact
from lattice.tools.browser import browser_snapshot as _browser_snapshot
from lattice.tools.groups._common import ToolBinding, ToolGroup, ToolsetT

_GROUP = "browser"


def register_interact(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def interact(
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
        return await _browser_interact(
            action,
            selector=selector,
            value=value,
            url=url,
            timeout_ms=timeout_ms,
        )

    return {"interact": interact}


def register_snapshot(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def snapshot(
        ctx: RunContext[TurnDeps],
        mode: str = "a11y",
        max_chars: int = 30_000,
    ) -> str:
        """Inspect the current Puppeteer/Chromium page before interacting.

        mode=a11y → accessibility / ARIA tree (best for finding click targets).
        mode=text → visible text dump of the body.
        """
        return await _browser_snapshot(mode=mode, max_chars=max_chars)

    return {"snapshot": snapshot}


GROUP = ToolGroup(
    name=_GROUP,
    description="Browser: drive and inspect a headless Chromium page.",
    bindings=(
        ToolBinding("interact", ToolTier.COLD, register_interact),
        ToolBinding("snapshot", ToolTier.COLD, register_snapshot),
    ),
)
