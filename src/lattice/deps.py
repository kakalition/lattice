"""Turn deps and shared tool runtime helpers (HITL, tracing, truncation)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from lattice.audit import audit_log
from lattice.config import LatticeSettings
from lattice.events import NullTurnEvents, TurnEvents
from lattice.hitl import ApprovalDecision, ApprovalRequest, HitlPort, tool_needs_approval
from lattice.mcp import McpHostManager
from lattice.memory import Memory
from lattice.profiles import Profile
from lattice.providers import FallbackCooldown
from lattice.session import SessionStore
from lattice.sqlite import SqlitePool, SqliteRegistry
from lattice.tools.todo import TodoList

CORE_TOOL_NAMES = [
    "shell",
    "read_file",
    "write_file",
    "edit_file",
    "search_files",
    "ocr",
    "generate_pdf",
    "generate_chart",
    "web_search",
    "web_fetch",
    "browser_interact",
    "browser_snapshot",
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
    "profile_list",
    "profile_remove",
    "tool_search",
    "tool_describe",
    "tool_invoke",
]


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
    outbound_media: list[Path] = field(default_factory=list)


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
