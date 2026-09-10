"""Shared guards for agent tool bindings."""

from __future__ import annotations

from pydantic_ai import Agent, RunContext

from lattice.deps import TurnDeps


def not_allowed(ctx: RunContext[TurnDeps], name: str) -> str | None:
    if name not in ctx.deps.enabled_tools:
        return "tool not allowed"
    return None


AgentT = Agent[TurnDeps, str]
