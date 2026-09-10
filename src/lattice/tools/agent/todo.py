"""Agent tool: todo."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def todo(ctx: RunContext[TurnDeps], action: str, text: str = "", item_id: int = 0) -> str:
        """In-session scratch checklist only — does NOT fire at a time.
        For timed reminders use schedule_add (run_at or cron)."""
        if err := not_allowed(ctx, "todo"):
            return err

        def _op() -> str:
            if action == "add":
                item = ctx.deps.todos.add(text)
                return f"added #{item.id} (session-only; not a timed reminder)"
            if action == "complete":
                item = ctx.deps.todos.complete(item_id)
                return f"completed #{item.id}" if item else "not found"
            return ctx.deps.todos.render()

        return await traced(ctx, "todo", {"action": action, "text": text, "item_id": item_id}, _op)

    return {"todo": todo}
