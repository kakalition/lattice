"""Depth-1 secondary worker agent (full primary tool parity, dispatched by primary)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from lattice.agent_app import build_core_toolset, tool_search_capability
from lattice.config import LatticeSettings
from lattice.deps import CORE_TOOL_NAMES
from lattice.providers.openai_compat import build_openai_model
from lattice.providers.settings import secondary_model_name
from lattice.runtime import set_cwd
from lattice.tools.deadline import with_deadline

if TYPE_CHECKING:
    from lattice.deps import TurnDeps

# `delegate` is deliberately excluded: only the primary may dispatch a worker.
SECONDARY_TOOL_NAMES = [n for n in CORE_TOOL_NAMES if n != "delegate"]

SECONDARY_SYSTEM_PROMPT = """\
You are Lattice's secondary worker. Complete the task the primary agent delegated to you,
using the same tools the primary has (minus `delegate`).
Return concise factual results for the primary agent. Do not address the end user.
Do not invent tools you lack. High-blast-radius actions (destructive shell, sqlite DDL/DML,
script execution) are approval-gated; ask only when the task actually requires them.
"""

_agent_cache: dict[str, Agent[Any, str]] = {}


def _build_secondary_agent(settings: LatticeSettings, model_id: str) -> Agent[Any, str]:
    from lattice.deps import TurnDeps

    agent: Agent[TurnDeps, str] = Agent(
        build_openai_model(settings, model_id),
        deps_type=TurnDeps,
        system_prompt=SECONDARY_SYSTEM_PROMPT,
        capabilities=[tool_search_capability()],
    )
    return agent


def get_secondary_agent(settings: LatticeSettings) -> Agent[Any, str]:
    """Reuse one Agent per secondary model id (stable tool schemas for caching).

    Toolsets are supplied per run (see ``run_secondary``) so the worker gets the
    same capability surface as the primary for that turn, minus ``delegate``.
    """
    mid = secondary_model_name(settings)
    cached = _agent_cache.get(mid)
    if cached is not None:
        return cached
    agent = _build_secondary_agent(settings, mid)
    _agent_cache[mid] = agent
    return agent


def clear_secondary_agent_cache() -> None:
    _agent_cache.clear()


async def run_secondary(
    deps: TurnDeps,
    *,
    task: str,
    context: str = "",
) -> str:
    """Run a depth-1 secondary worker. Refuses nested delegate."""
    from lattice.deps import TurnDeps as TD
    from lattice.deps import truncate_result

    if getattr(deps, "delegate_depth", 0) > 0:
        return "error: nested delegate is not allowed"
    task = task.strip()
    if not task:
        return "error: task is required"

    settings = deps.settings
    model_id = secondary_model_name(settings, profile_secondary=deps.profile.secondary_model)
    agent = get_secondary_agent(settings)
    # Same capability surface as the primary, minus the dispatch entry. The
    # primary's policy remains the ceiling and is applied by the deps-driven filter.
    toolsets = [
        build_core_toolset(settings, deps.mcp, exclude=frozenset({"delegate"}))
    ]

    secondary_deps = TD(
        settings=deps.settings,
        profile=deps.profile,
        hitl=deps.hitl,
        session=deps.session,
        session_id=deps.session_id,
        memory=deps.memory,
        sqlite_registry=deps.sqlite_registry,
        sqlite_pool=deps.sqlite_pool,
        mcp=deps.mcp,
        events=deps.events,
        todos=deps.todos,
        workspace=deps.workspace,
        approval_memory=deps.approval_memory,
        consecutive_denials=deps.consecutive_denials,
        # The primary's policy is the ceiling; `delegate` never propagates.
        enabled_tools=[n for n in deps.enabled_tools if n != "delegate"],
        skills=deps.skills,
        user_id=deps.user_id,
        channel=deps.channel,
        cooldown=deps.cooldown,
        delegate_depth=deps.delegate_depth + 1,
    )

    user_prompt = task if not context.strip() else f"{task}\n\n## Context\n{context.strip()}"
    deadline = float(settings.agent.idle_watchdog_seconds)
    limits = UsageLimits(request_limit=max(1, int(settings.agent.secondary_max_iterations)))

    async def _run() -> str:
        set_cwd(deps.workspace)
        result = await agent.run(
            user_prompt,
            deps=secondary_deps,
            usage_limits=limits,
            model=build_openai_model(settings, model_id),
            toolsets=toolsets,
        )
        return truncate_result(str(result.output))

    try:
        return await with_deadline(_run(), seconds=deadline, label="secondary")
    except Exception as exc:
        return f"secondary error: {exc}"
