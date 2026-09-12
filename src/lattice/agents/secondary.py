"""Depth-1 secondary worker agent (full primary tool parity, dispatched by primary)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from lattice.agent_app import build_core_toolset, tool_search_capability
from lattice.config import LatticeSettings
from lattice.deps import CORE_TOOL_NAMES
from lattice.providers.caching import prompt_cache_settings, session_routing_settings
from lattice.providers.openai_compat import build_openai_model
from lattice.providers.settings import secondary_model_name
from lattice.providers.usage import usage_to_dict
from lattice.runtime import set_cwd
from lattice.tools.deadline import with_deadline

if TYPE_CHECKING:
    from lattice.deps import TurnDeps

logger = logging.getLogger("lattice.secondary")

# `delegate` is deliberately excluded: only the primary may dispatch a worker.
SECONDARY_TOOL_NAMES = [n for n in CORE_TOOL_NAMES if n != "delegate"]

SECONDARY_SYSTEM_PROMPT = """\
You are Lattice's secondary worker. Complete the task the primary agent delegated to you,
using the same tools the primary has (minus `delegate`).
Return concise factual results for the primary agent. Do not address the end user.
Do not invent tools you lack. High-blast-radius actions (destructive shell, sqlite DDL/DML,
script execution) are approval-gated; ask only when the task actually requires them.
"""

# User-facing counterpart used by the whole-turn router's LOW branch.
WORKER_SYSTEM_PROMPT = """\
You are Lattice's worker, answering the user directly for a bounded single-pass task.
Use the same tools the primary has (minus `delegate`), but only when the task needs them.
Be concise and complete, address the user in their language, and do not invent tools you
lack. High-blast-radius actions (destructive shell, sqlite DDL/DML, script execution) are
approval-gated; ask only when the task actually requires them.
"""

_agent_cache: dict[tuple[str, str], Agent[Any, str]] = {}


def _build_secondary_agent(
    settings: LatticeSettings,
    model_id: str,
    system_prompt: str = SECONDARY_SYSTEM_PROMPT,
) -> Agent[Any, str]:
    from lattice.deps import TurnDeps

    agent: Agent[TurnDeps, str] = Agent(
        build_openai_model(settings, model_id),
        deps_type=TurnDeps,
        system_prompt=system_prompt,
        capabilities=[tool_search_capability()],
    )
    return agent


def get_secondary_agent(
    settings: LatticeSettings,
    *,
    model_id: str | None = None,
    system_prompt: str | None = None,
) -> Agent[Any, str]:
    """Reuse one Agent per ``(model id, system prompt)`` (stable tool schemas for caching).

    The prompt is part of the key so the delegate-path secondary and the user-facing
    worker do not collide when they share a model id. Toolsets are supplied per run
    (see ``_secondary_once``) so the worker gets the primary's capability surface
    minus ``delegate``.
    """
    mid = model_id or secondary_model_name(settings)
    prompt = system_prompt or SECONDARY_SYSTEM_PROMPT
    key = (mid, prompt)
    cached = _agent_cache.get(key)
    if cached is not None:
        return cached
    agent = _build_secondary_agent(settings, mid, prompt)
    _agent_cache[key] = agent
    return agent


def clear_secondary_agent_cache() -> None:
    _agent_cache.clear()


async def _secondary_once(
    deps: TurnDeps,
    *,
    task: str,
    context: str = "",
    system_prompt: str | None = None,
) -> tuple[str, RunUsage]:
    """Single depth-1 agent run. Returns raw output + usage; does not swallow errors."""
    from lattice.deps import TurnDeps as TD

    settings = deps.settings
    model_id = secondary_model_name(settings, profile_secondary=deps.profile.secondary_model)
    if system_prompt is None:
        agent = get_secondary_agent(settings)
    else:
        agent = get_secondary_agent(settings, model_id=model_id, system_prompt=system_prompt)
    # Same capability surface as the primary, minus the dispatch entry. The
    # primary's policy remains the ceiling and is applied by the deps-driven filter.
    toolsets = [
        build_core_toolset(
            settings,
            deps.mcp,
            exclude=frozenset({"delegate"}),
            user_tools=deps.user_tools,
        )
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
        user_tools=deps.user_tools,
        user_id=deps.user_id,
        channel=deps.channel,
        cooldown=deps.cooldown,
        delegate_depth=deps.delegate_depth + 1,
    )

    user_prompt = task if not context.strip() else f"{task}\n\n## Context\n{context.strip()}"
    limits = UsageLimits(request_limit=max(1, int(settings.agent.secondary_max_iterations)))

    set_cwd(deps.workspace)
    model_obj = build_openai_model(settings, model_id)
    model_settings = cast(
        ModelSettings,
        {
            **prompt_cache_settings(settings, model_obj),
            **session_routing_settings(settings, deps.session_id),
        },
    )
    result = await agent.run(
        user_prompt,
        deps=secondary_deps,
        usage_limits=limits,
        model=model_obj,
        toolsets=toolsets,
        model_settings=model_settings or None,
    )
    logger.debug(
        "secondary usage: %s",
        usage_to_dict(getattr(result, "usage", None), model=model_id),
    )
    usage = getattr(result, "usage", None) or RunUsage()
    return str(result.output), usage


async def run_secondary(
    deps: TurnDeps,
    *,
    task: str,
    context: str = "",
    system_prompt: str | None = None,
    user_facing: bool = False,
) -> str:
    """Run a depth-1 secondary worker. Refuses nested delegate.

    Defaults preserve the delegate-tool contract ("do not address the user").
    ``user_facing=True`` returns untruncated text for the whole-turn router.
    """
    from lattice.deps import truncate_result

    if getattr(deps, "delegate_depth", 0) > 0:
        return "error: nested delegate is not allowed"
    task = task.strip()
    if not task:
        return "error: task is required"

    settings = deps.settings
    deadline = float(settings.agent.idle_watchdog_seconds)
    try:
        text, _ = await with_deadline(
            _secondary_once(deps, task=task, context=context, system_prompt=system_prompt),
            seconds=deadline,
            label="secondary",
        )
    except Exception as exc:
        return f"secondary error: {exc}"
    return text if user_facing else truncate_result(text)


async def run_worker(
    deps: TurnDeps,
    *,
    task: str,
    system_prompt: str = WORKER_SYSTEM_PROMPT,
) -> tuple[str, RunUsage]:
    """User-facing worker for the whole-turn router's LOW branch.

    Propagates errors (instead of returning an error string) so the caller can
    fall back to the primary HIGH path. The caller owns the deadline.
    """
    if getattr(deps, "delegate_depth", 0) > 0:
        raise RuntimeError("nested delegate is not allowed")
    task = task.strip()
    if not task:
        raise ValueError("task is required")
    return await _secondary_once(deps, task=task, system_prompt=system_prompt)
