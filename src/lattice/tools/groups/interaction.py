"""Built-in ``interaction`` group — clarification and in-session todos."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.tools.clarify import clarify as _clarify
from lattice.tools.groups._common import ToolBinding, ToolGroup, ToolsetT

_GROUP = "interaction"


def register_clarify(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def clarify(
        ctx: RunContext[TurnDeps], question: str, choices: list[str] | None = None
    ) -> str:
        return await _clarify(question, ctx.deps.hitl, choices=choices)

    return {"clarify": clarify}


def register_todo(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def todo(ctx: RunContext[TurnDeps], action: str, text: str = "", item_id: int = 0) -> str:
        """In-session scratch checklist only — does NOT fire at a time.
        For timed reminders use schedule_add (run_at or cron)."""
        if action == "add":
            item = ctx.deps.todos.add(text)
            return f"added #{item.id} (session-only; not a timed reminder)"
        if action == "complete":
            item = ctx.deps.todos.complete(item_id)
            return f"completed #{item.id}" if item else "not found"
        return ctx.deps.todos.render()

    return {"todo": todo}


GROUP = ToolGroup(
    name=_GROUP,
    description="Interaction: ask clarifying questions and track in-session todos.",
    bindings=(
        ToolBinding("clarify", ToolTier.EAGER, register_clarify),
        ToolBinding("todo", ToolTier.EAGER, register_todo),
    ),
)
