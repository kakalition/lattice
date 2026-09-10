"""Agent deps and Pydantic AI tool registration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, RunContext

from lattice.audit import audit_log
from lattice.config import LatticeSettings
from lattice.events import NullTurnEvents, TurnEvents
from lattice.hitl import ApprovalDecision, ApprovalRequest, HitlPort, tool_needs_approval
from lattice.mcp import McpHostManager, should_defer_mcp, tool_describe, tool_invoke, tool_search
from lattice.memory import Memory, build_memory
from lattice.memory.tools import memory_add, memory_forget, memory_search, memory_update
from lattice.profiles import Profile, merge_tool_policy
from lattice.prompt import PromptBundle, build_skill_index_xml
from lattice.providers import FallbackCooldown, build_openai_model
from lattice.scheduler.tools import (
    schedule_add,
    schedule_cancel,
    schedule_list,
    timezone_get,
    timezone_set,
)
from lattice.session import SessionStore
from lattice.skills import skill_view, skills_list
from lattice.sqlite import (
    SqlitePool,
    SqliteRegistry,
    sqlite_backup,
    sqlite_execute,
    sqlite_list,
    sqlite_query,
    sqlite_register,
    sqlite_schema,
    sqlite_unregister,
)
from lattice.tools.clarify import clarify as clarify_tool
from lattice.tools.file import edit_file, read_file, search_files, write_file
from lattice.tools.session_search import session_search
from lattice.tools.shell import run_shell
from lattice.tools.todo import TodoList
from lattice.tools.web import web_fetch, web_search


@dataclass
class TurnDeps:
    settings: LatticeSettings
    profile: Profile
    hitl: HitlPort
    session: SessionStore
    session_id: str
    memory: Memory
    sqlite_registry: SqliteRegistry
    sqlite_pool: SqlitePool
    mcp: McpHostManager
    events: TurnEvents = field(default_factory=NullTurnEvents)
    todos: TodoList = field(default_factory=TodoList)
    workspace: Path = field(default_factory=Path.cwd)
    approval_memory: set[str] = field(default_factory=set)
    consecutive_denials: int = 0
    enabled_tools: list[str] = field(default_factory=list)
    skills: list = field(default_factory=list)
    user_id: str = "local"
    channel: str = "cli"
    cooldown: FallbackCooldown = field(default_factory=FallbackCooldown)
    delegate_depth: int = 0


CORE_TOOL_NAMES = [
    "shell",
    "read_file",
    "write_file",
    "edit_file",
    "search_files",
    "web_search",
    "web_fetch",
    "clarify",
    "todo",
    "delegate",
    "schedule_add",
    "schedule_list",
    "schedule_cancel",
    "timezone_get",
    "timezone_set",
    "session_search",
    "memory_search",
    "memory_add",
    "memory_update",
    "memory_forget",
    "sqlite_list",
    "sqlite_schema",
    "sqlite_query",
    "sqlite_execute",
    "sqlite_register",
    "sqlite_unregister",
    "sqlite_backup",
    "skills_list",
    "skill_view",
    "tool_search",
    "tool_describe",
    "tool_invoke",
]


def truncate_result(text: str, limit: int = 30_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


async def traced(
    ctx: RunContext[TurnDeps],
    name: str,
    args: dict[str, Any],
    op: Any,
) -> str:
    """Run a tool body with start/end turn events (feeds the process log)."""
    await ctx.deps.events.on_tool_start(name, args)
    try:
        result = op()
        if hasattr(result, "__await__"):
            out = await result
        else:
            out = result
    except Exception as exc:
        out = f"error: {exc}"
    if not isinstance(out, str):
        out = str(out)
    out = truncate_result(out)
    await ctx.deps.events.on_tool_end(name, out)
    return out


async def maybe_approve(
    ctx: RunContext[TurnDeps], tool_name: str, summary: str, **args: Any
) -> str | None:
    if not tool_needs_approval(tool_name, args=args):
        return None
    key = f"{tool_name}:{summary}"
    if key in ctx.deps.approval_memory:
        return None
    await ctx.deps.events.on_status(f"hitl_ask {tool_name}: {summary[:200]}")
    decision = await ctx.deps.hitl.approve(
        ApprovalRequest(tool_name=tool_name, summary=summary, detail=str(args)[:500])
    )
    await ctx.deps.events.on_status(f"hitl_decision {tool_name}: {decision.value}")
    audit_log(
        "hitl_decision",
        {
            "tool": tool_name,
            "decision": decision.value,
            "session_id": ctx.deps.session_id,
            "profile_id": ctx.deps.profile.id,
        },
        home=ctx.deps.settings.home,
    )
    if decision == ApprovalDecision.APPROVE:
        ctx.deps.approval_memory.add(key)
        ctx.deps.consecutive_denials = 0
        return None
    ctx.deps.consecutive_denials += 1
    if ctx.deps.consecutive_denials >= 3:
        return "denied (consecutive denial breaker)"
    return f"denied: {decision.value}"


def build_prompt_bundle(
    profile: Profile, skills_entries: list[tuple[str, str]], notices: list[str]
) -> PromptBundle:
    identity = profile.soul.strip() or "You are Lattice."
    context = profile.user_notes.strip()
    return PromptBundle(
        identity=identity,
        context=context,
        skill_index=build_skill_index_xml(skills_entries),
        notices=notices,
    )


def create_agent(
    settings: LatticeSettings,
    profile: Profile,
    *,
    system_prompt: str,
    model: Any | None = None,
) -> Agent[TurnDeps, str]:
    from lattice.providers.settings import resolve_model_id

    primary = profile.primary_model or profile.model
    resolved = model or build_openai_model(settings, resolve_model_id(settings, profile_model=primary))
    agent: Agent[TurnDeps, str] = Agent(resolved, deps_type=TurnDeps, system_prompt=system_prompt)

    @agent.tool
    async def shell(ctx: RunContext[TurnDeps], command: str, timeout: float = 60.0) -> str:
        if "shell" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        denied = await maybe_approve(ctx, "shell", command, command=command)
        if denied:
            return denied

        async def _op() -> str:
            try:
                result = await run_shell(command, timeout=timeout)
                out = truncate_result(f"exit={result.exit_code}\n{result.stdout}\n{result.stderr}")
            except Exception as exc:
                out = f"shell error: {exc}"
            audit_log("tool", {"name": "shell", "command": command}, home=ctx.deps.settings.home)
            return out

        return await traced(ctx, "shell", {"command": command, "timeout": timeout}, _op)

    @agent.tool
    async def read_file_tool(ctx: RunContext[TurnDeps], path: str) -> str:
        if "read_file" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "read_file",
            {"path": path},
            lambda: read_file(path, workspace=ctx.deps.workspace),
        )

    @agent.tool
    async def write_file_tool(ctx: RunContext[TurnDeps], path: str, content: str) -> str:
        if "write_file" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        denied = await maybe_approve(ctx, "write_file", path, path=path)
        if denied:
            return denied
        return await traced(
            ctx,
            "write_file",
            {"path": path, "content": content},
            lambda: write_file(path, content, workspace=ctx.deps.workspace),
        )

    @agent.tool
    async def edit_file_tool(
        ctx: RunContext[TurnDeps], path: str, old_string: str, new_string: str
    ) -> str:
        if "edit_file" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        denied = await maybe_approve(ctx, "edit_file", path, path=path)
        if denied:
            return denied
        return await traced(
            ctx,
            "edit_file",
            {"path": path, "old_string": old_string, "new_string": new_string},
            lambda: edit_file(path, old_string, new_string, workspace=ctx.deps.workspace),
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
    async def clarify(
        ctx: RunContext[TurnDeps], question: str, choices: list[str] | None = None
    ) -> str:
        if "clarify" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "clarify",
            {"question": question, "choices": choices},
            lambda: clarify_tool(question, ctx.deps.hitl, choices=choices),
        )

    @agent.tool
    async def todo(ctx: RunContext[TurnDeps], action: str, text: str = "", item_id: int = 0) -> str:
        """In-session scratch checklist only — does NOT fire at a time.
        For timed reminders use schedule_add (run_at or cron)."""
        if "todo" not in ctx.deps.enabled_tools:
            return "tool not allowed"

        def _op() -> str:
            if action == "add":
                item = ctx.deps.todos.add(text)
                return f"added #{item.id} (session-only; not a timed reminder)"
            if action == "complete":
                item = ctx.deps.todos.complete(item_id)
                return f"completed #{item.id}" if item else "not found"
            return ctx.deps.todos.render()

        return await traced(ctx, "todo", {"action": action, "text": text, "item_id": item_id}, _op)

    @agent.tool
    async def delegate_tool(ctx: RunContext[TurnDeps], task: str, context: str = "") -> str:
        """Delegate a bounded research/analysis task to the secondary worker model.
        Use for web/file/sqlite lookups; synthesize the result yourself for the user."""
        if "delegate" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        if ctx.deps.delegate_depth > 0:
            return "error: nested delegate is not allowed"
        from lattice.agents.secondary import run_secondary
        from lattice.providers.settings import secondary_model_name

        mid = secondary_model_name(
            ctx.deps.settings, profile_secondary=ctx.deps.profile.secondary_model
        )
        return await traced(
            ctx,
            "delegate",
            {"task": task, "context": context, "secondary_model": mid},
            lambda: run_secondary(ctx.deps, task=task, context=context),
        )

    @agent.tool
    async def schedule_add_tool(
        ctx: RunContext[TurnDeps],
        reminder: str,
        run_at: str = "",
        cron: str = "",
        timezone: str = "",
        deliver: str = "telegram",
        job_id: str = "",
    ) -> str:
        """Schedule a timed reminder. One-shot: run_at as local wall time
        (e.g. 2026-09-10T22:45:00) — Lattice applies the saved timezone automatically.
        Or include an explicit offset (…+07:00). Recurring: cron five fields in timezone.
        Never pass timezone='' to force UTC; omit timezone to use config. Do not ask the
        user for timezone unless they want to change it (use timezone_set).
        Prefer this over todo for anything time-based. deliver=telegram|cli|none."""
        if "schedule_add" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        tz = timezone or ctx.deps.settings.timezone
        return await traced(
            ctx,
            "schedule_add",
            {
                "reminder": reminder,
                "run_at": run_at,
                "cron": cron,
                "timezone": tz,
                "deliver": deliver,
                "job_id": job_id,
            },
            lambda: schedule_add(
                reminder=reminder,
                home=ctx.deps.settings.home,
                run_at=run_at,
                cron=cron,
                timezone=tz,
                deliver=deliver,
                profile=ctx.deps.profile.id,
                job_id=job_id,
            ),
        )

    @agent.tool
    async def schedule_list_tool(ctx: RunContext[TurnDeps]) -> str:
        """List scheduled reminder jobs."""
        if "schedule_list" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(ctx, "schedule_list", {}, lambda: schedule_list(home=ctx.deps.settings.home))

    @agent.tool
    async def schedule_cancel_tool(ctx: RunContext[TurnDeps], job_id: str) -> str:
        """Cancel a scheduled job by id."""
        if "schedule_cancel" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "schedule_cancel",
            {"job_id": job_id},
            lambda: schedule_cancel(job_id, home=ctx.deps.settings.home),
        )

    @agent.tool
    async def timezone_get_tool(ctx: RunContext[TurnDeps]) -> str:
        """Show the remembered IANA timezone used for reminders."""
        if "timezone_get" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx, "timezone_get", {}, lambda: timezone_get(home=ctx.deps.settings.home)
        )

    @agent.tool
    async def timezone_set_tool(ctx: RunContext[TurnDeps], timezone: str) -> str:
        """Persist the user's IANA timezone (e.g. Asia/Ho_Chi_Minh) for all reminders."""
        if "timezone_set" not in ctx.deps.enabled_tools:
            return "tool not allowed"

        def _set() -> str:
            result = timezone_set(timezone, home=ctx.deps.settings.home)
            if result.startswith("timezone saved:"):
                ctx.deps.settings.timezone = timezone.strip()
            return result

        return await traced(ctx, "timezone_set", {"timezone": timezone}, _set)

    @agent.tool
    async def session_search_tool(ctx: RunContext[TurnDeps], query: str) -> str:
        if "session_search" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx, "session_search", {"query": query}, lambda: session_search(query, ctx.deps.session)
        )

    @agent.tool
    async def memory_search_tool(ctx: RunContext[TurnDeps], query: str) -> str:
        if "memory_search" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx, "memory_search", {"query": query}, lambda: memory_search(ctx.deps.memory, query)
        )

    @agent.tool
    async def memory_add_tool(ctx: RunContext[TurnDeps], text: str) -> str:
        if "memory_add" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(ctx, "memory_add", {"text": text}, lambda: memory_add(ctx.deps.memory, text))

    @agent.tool
    async def memory_update_tool(ctx: RunContext[TurnDeps], memory_id: str, text: str) -> str:
        if "memory_update" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "memory_update",
            {"memory_id": memory_id, "text": text},
            lambda: memory_update(ctx.deps.memory, memory_id, text),
        )

    @agent.tool
    async def memory_forget_tool(ctx: RunContext[TurnDeps], memory_id: str) -> str:
        if "memory_forget" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "memory_forget",
            {"memory_id": memory_id},
            lambda: memory_forget(ctx.deps.memory, memory_id),
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
    async def sqlite_schema_tool(ctx: RunContext[TurnDeps], name: str) -> str:
        if "sqlite_schema" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "sqlite_schema",
            {"name": name},
            lambda: sqlite_schema(ctx.deps.sqlite_pool, name, ctx.deps.profile.sqlite_allow),
        )

    @agent.tool
    async def sqlite_query_tool(ctx: RunContext[TurnDeps], name: str, sql: str) -> str:
        if "sqlite_query" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "sqlite_query",
            {"name": name, "sql": sql},
            lambda: sqlite_query(
                ctx.deps.sqlite_pool,
                name,
                sql,
                allow=ctx.deps.profile.sqlite_allow,
                row_limit=ctx.deps.settings.sqlite.query_row_limit,
            ),
        )

    @agent.tool
    async def sqlite_execute_tool(
        ctx: RunContext[TurnDeps], name: str, sql: str, dry_run: bool = False
    ) -> str:
        if "sqlite_execute" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        denied = await maybe_approve(
            ctx, "sqlite_execute", f"{name}: {sql[:120]}", name=name, sql=sql
        )
        if denied:
            return denied
        return await traced(
            ctx,
            "sqlite_execute",
            {"name": name, "sql": sql, "dry_run": dry_run},
            lambda: sqlite_execute(
                ctx.deps.sqlite_pool,
                name,
                sql,
                allow=ctx.deps.profile.sqlite_allow,
                dry_run=dry_run,
            ),
        )

    @agent.tool
    async def sqlite_register_tool(
        ctx: RunContext[TurnDeps], name: str, path: str, read_only: bool = False
    ) -> str:
        if "sqlite_register" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        denied = await maybe_approve(
            ctx, "sqlite_register", f"{name}->{path}", name=name, path=path
        )
        if denied:
            return denied
        return await traced(
            ctx,
            "sqlite_register",
            {"name": name, "path": path, "read_only": read_only},
            lambda: sqlite_register(ctx.deps.sqlite_registry, name, path, read_only=read_only),
        )

    @agent.tool
    async def sqlite_unregister_tool(ctx: RunContext[TurnDeps], name: str) -> str:
        if "sqlite_unregister" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        denied = await maybe_approve(ctx, "sqlite_unregister", name, name=name)
        if denied:
            return denied
        return await traced(
            ctx,
            "sqlite_unregister",
            {"name": name},
            lambda: sqlite_unregister(ctx.deps.sqlite_registry, name),
        )

    @agent.tool
    async def sqlite_backup_tool(ctx: RunContext[TurnDeps], name: str) -> str:
        if "sqlite_backup" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "sqlite_backup",
            {"name": name},
            lambda: sqlite_backup(ctx.deps.sqlite_registry, name, ctx.deps.profile.sqlite_allow),
        )

    @agent.tool
    async def skills_list_tool(ctx: RunContext[TurnDeps]) -> str:
        if "skills_list" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "skills_list",
            {},
            lambda: skills_list(
                ctx.deps.skills,
                prefer=ctx.deps.profile.skills_prefer,
                disable=ctx.deps.profile.skills_disable,
            ),
        )

    @agent.tool
    async def skill_view_tool(ctx: RunContext[TurnDeps], name: str) -> str:
        if "skill_view" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx, "skill_view", {"name": name}, lambda: skill_view(name, ctx.deps.skills)
        )

    @agent.tool
    async def tool_search_tool(ctx: RunContext[TurnDeps], query: str) -> str:
        if "tool_search" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx, "tool_search", {"query": query}, lambda: tool_search(ctx.deps.mcp, query)
        )

    @agent.tool
    async def tool_describe_tool(ctx: RunContext[TurnDeps], name: str) -> str:
        if "tool_describe" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx, "tool_describe", {"name": name}, lambda: tool_describe(ctx.deps.mcp, name)
        )

    @agent.tool
    async def tool_invoke_tool(
        ctx: RunContext[TurnDeps], name: str, arguments: dict[str, Any] | None = None
    ) -> str:
        if "tool_invoke" not in ctx.deps.enabled_tools:
            return "tool not allowed"
        return await traced(
            ctx,
            "tool_invoke",
            {"name": name, "arguments": arguments or {}},
            lambda: tool_invoke(ctx.deps.mcp, name, arguments),
        )

    # Alias names expected by policy strings
    agent.tool_functions = {  # type: ignore[attr-defined]
        "shell": shell,
        "read_file": read_file_tool,
        "write_file": write_file_tool,
        "edit_file": edit_file_tool,
        "search_files": search_files_tool,
        "web_search": web_search_tool,
        "web_fetch": web_fetch_tool,
        "clarify": clarify,
        "todo": todo,
        "delegate": delegate_tool,
        "schedule_add": schedule_add_tool,
        "schedule_list": schedule_list_tool,
        "schedule_cancel": schedule_cancel_tool,
        "timezone_get": timezone_get_tool,
        "timezone_set": timezone_set_tool,
        "session_search": session_search_tool,
        "memory_search": memory_search_tool,
        "memory_add": memory_add_tool,
        "memory_update": memory_update_tool,
        "memory_forget": memory_forget_tool,
        "sqlite_list": sqlite_list_tool,
        "sqlite_schema": sqlite_schema_tool,
        "sqlite_query": sqlite_query_tool,
        "sqlite_execute": sqlite_execute_tool,
        "sqlite_register": sqlite_register_tool,
        "sqlite_unregister": sqlite_unregister_tool,
        "sqlite_backup": sqlite_backup_tool,
        "skills_list": skills_list_tool,
        "skill_view": skill_view_tool,
        "tool_search": tool_search_tool,
        "tool_describe": tool_describe_tool,
        "tool_invoke": tool_invoke_tool,
    }
    return agent


def resolve_enabled_tools(
    settings: LatticeSettings,
    profile: Profile,
    *,
    channel: str,
    mcp: McpHostManager,
) -> list[str]:
    channel_cfg = settings.telegram.tools if channel == "telegram" else settings.tools
    names = list(CORE_TOOL_NAMES)
    if not should_defer_mcp(mcp, settings.tools) and not mcp.enabled_tools():
        # no bridge needed if no MCP tools
        names = [n for n in names if n not in {"tool_search", "tool_describe", "tool_invoke"}]
    elif not mcp.enabled_tools():
        names = [n for n in names if n not in {"tool_search", "tool_describe", "tool_invoke"}]
    return merge_tool_policy(
        names,
        profile_allow=profile.tools_allow,
        profile_deny=profile.tools_deny,
        channel_allow=channel_cfg.allow if hasattr(channel_cfg, "allow") else settings.tools.allow,
        channel_deny=channel_cfg.deny if hasattr(channel_cfg, "deny") else settings.tools.deny,
    )


def build_memory_for_profile(settings: LatticeSettings, profile: Profile) -> Memory:
    from lattice.providers.settings import apply_provider_env

    apply_provider_env(settings.provider)
    collection = profile.memory_collection or f"lattice-{profile.id}"
    return build_memory(collection=collection, path=settings.home / "qdrant")
