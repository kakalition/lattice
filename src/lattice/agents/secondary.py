"""Depth-1 secondary worker agent (cache-stable system + fixed tools)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from lattice.config import LatticeSettings
from lattice.providers.openai_compat import build_openai_model
from lattice.providers.settings import secondary_model_name
from lattice.sqlite import sqlite_list, sqlite_query, sqlite_schema
from lattice.tools.deadline import with_deadline
from lattice.tools.file import read_file, search_files
from lattice.tools.ocr import ocr_image
from lattice.tools.web import web_fetch, web_search

if TYPE_CHECKING:
    from lattice.deps import TurnDeps

# Fixed order — do not reorder (prompt-cache / tool schema stability).
SECONDARY_TOOL_NAMES = [
    "web_search",
    "web_fetch",
    "read_file",
    "search_files",
    "ocr",
    "sqlite_list",
    "sqlite_schema",
    "sqlite_query",
]

SECONDARY_SYSTEM_PROMPT = """\
You are Lattice's secondary worker. Complete the delegated task using only your tools.
Return concise factual results for the primary agent. Do not address the end user.
Do not invent tools you lack. Prefer read-only actions.
"""

_agent_cache: dict[str, Agent[Any, str]] = {}


def _build_secondary_agent(settings: LatticeSettings, model_id: str) -> Agent[Any, str]:
    from lattice.deps import TurnDeps, traced

    agent: Agent[TurnDeps, str] = Agent(
        build_openai_model(settings, model_id),
        deps_type=TurnDeps,
        system_prompt=SECONDARY_SYSTEM_PROMPT,
    )

    @agent.tool
    async def web_search_tool(ctx: RunContext[TurnDeps], query: str) -> str:
        if "web_search" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "web_search",
            {"query": query},
            lambda: web_search(query, api_key=ctx.deps.settings.tavily_api_key),
        )

    @agent.tool
    async def web_fetch_tool(ctx: RunContext[TurnDeps], url: str) -> str:
        if "web_fetch" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(ctx, "web_fetch", {"url": url}, lambda: web_fetch(url))

    @agent.tool
    async def read_file_tool(ctx: RunContext[TurnDeps], path: str) -> str:
        if "read_file" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "read_file",
            {"path": path},
            lambda: read_file(
                path, workspace=ctx.deps.workspace, home=ctx.deps.settings.home
            ),
        )

    @agent.tool
    async def search_files_tool(ctx: RunContext[TurnDeps], pattern: str, glob: str = "**/*") -> str:
        if "search_files" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "search_files",
            {"pattern": pattern, "glob": glob},
            lambda: search_files(pattern, workspace=ctx.deps.workspace, glob=glob),
        )

    @agent.tool
    async def ocr_tool(ctx: RunContext[TurnDeps], path: str) -> str:
        if "ocr" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "ocr",
            {"path": path},
            lambda: ocr_image(path, workspace=ctx.deps.workspace, home=ctx.deps.settings.home),
        )

    @agent.tool
    async def sqlite_list_tool(ctx: RunContext[TurnDeps]) -> str:
        if "sqlite_list" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "sqlite_list",
            {},
            lambda: sqlite_list(ctx.deps.sqlite_registry, ctx.deps.profile.sqlite_allow),
        )

    @agent.tool
    async def sqlite_schema_tool(ctx: RunContext[TurnDeps], db: str) -> str:
        if "sqlite_schema" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "sqlite_schema",
            {"db": db},
            lambda: sqlite_schema(ctx.deps.sqlite_pool, db, ctx.deps.profile.sqlite_allow),
        )

    @agent.tool
    async def sqlite_query_tool(ctx: RunContext[TurnDeps], db: str, sql: str) -> str:
        if "sqlite_query" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "sqlite_query",
            {"db": db, "sql": sql},
            lambda: sqlite_query(
                ctx.deps.sqlite_pool,
                db,
                sql,
                allow=ctx.deps.profile.sqlite_allow,
                row_limit=ctx.deps.settings.sqlite.query_row_limit,
            ),
        )

    return agent


def get_secondary_agent(settings: LatticeSettings) -> Agent[Any, str]:
    """Reuse one Agent per secondary model id (stable tool schemas for caching)."""
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
        enabled_tools=list(SECONDARY_TOOL_NAMES),
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
        result = await agent.run(
            user_prompt,
            deps=secondary_deps,
            usage_limits=limits,
            model=build_openai_model(settings, model_id),
        )
        return truncate_result(str(result.output))

    try:
        return await with_deadline(_run(), seconds=deadline, label="secondary")
    except Exception as exc:
        return f"secondary error: {exc}"
