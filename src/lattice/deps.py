"""Turn deps and shared tool runtime helpers (HITL, tracing, truncation)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext

from lattice.audit import audit_log
from lattice.config import LatticeSettings
from lattice.events import NullTurnEvents, TurnEvents
from lattice.hitl import ApprovalDecision, ApprovalRequest, HitlPort, tool_needs_approval
from lattice.mcp import McpHostManager
from lattice.memory import Memory
from lattice.profiles import Profile
from lattice.session import SessionStore
from lattice.sqlite import SqlitePool, SqliteRegistry
from lattice.tools.todo import TodoList

CORE_TOOL_NAMES = [
    "shell",
    "read_file",
    "write_file",
    "edit_file",
    "remove_path",
    "search_files",
    "ocr",
    "generate_pdf",
    "generate_chart",
    "web_search",
    "web_fetch",
    "browser_interact",
    "browser_snapshot",
    "execute_script",
    "clarify",
    "calculator",
    "todo",
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
]


class ApprovalMemory(set[str]):
    """Set of approved HITL keys.

    Subclasses ``set`` so pydantic keeps the *same instance* on assignment (a plain
    ``set[str]`` field is copied on validation, which would break the deliberate
    sharing of approval memory across nested tool calls).
    """


class TurnDeps(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    settings: LatticeSettings
    profile: Profile
    hitl: HitlPort
    session: SessionStore
    session_id: str
    memory: Memory
    sqlite_registry: SqliteRegistry
    sqlite_pool: SqlitePool
    mcp: McpHostManager
    events: TurnEvents = Field(default_factory=NullTurnEvents)
    todos: TodoList = Field(default_factory=TodoList)
    workspace: Path = Field(default_factory=Path.cwd)
    approval_memory: ApprovalMemory = Field(default_factory=ApprovalMemory)
    consecutive_denials: int = 0
    # "tool:args" -> failure count, used to break repeated identical failures.
    tool_failures: dict[str, int] = Field(default_factory=dict)
    enabled_tools: list[str] = Field(default_factory=list)
    skills: list = Field(default_factory=list)
    user_tools: list = Field(default_factory=list)
    user_id: str = "local"
    channel: str = "cli"
    # Namespaces per-turn caches (e.g. read elision) so cross-turn reads re-serve.
    turn_id: str = ""
    outbound_media: list[Path] = Field(default_factory=list)


def _write_scratch(text: str, scratch_dir: Path) -> str:
    digest = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:16]
    path = scratch_dir / f"{digest}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def truncate_result(text: str, limit: int = 12_000, *, scratch_dir: Path | None = None) -> str:
    """Bound a tool result; when ``scratch_dir`` is given keep head+tail and a ref.

    A single huge result otherwise poisons every later step's context. The full
    text is written to a scratch file the model can read by range.
    """
    if len(text) <= limit:
        return text
    if scratch_dir is None:
        return text[:limit] + "\n[truncated]"
    try:
        ref = _write_scratch(text, scratch_dir)
    except Exception:
        return text[:limit] + "\n[truncated]"
    head = int(limit * 0.7)
    tail = limit - head
    hidden = len(text) - limit
    return text[:head] + f"\n...[truncated {hidden} chars; full result: {ref}]...\n" + text[-tail:]


def _failure_key(name: str, args: dict[str, Any]) -> str:
    return f"{name}:{repr(args)[:200]}"


# A tool result is a failure when it announces one. Kept as one predicate so the
# failure breaker, the turn record, and the action ledger agree. ``unavailable:``
# is checked anywhere on the first line as a safety net for producers that
# predate the ``error:`` prefix (e.g. "<tool> unavailable: ...").
_FAILURE_PREFIXES = ("error:", "denied")


def result_failed(text: str) -> bool:
    """True when a tool result string represents a failure."""
    low = (text or "").lstrip().lower()
    head = low.split("\n", 1)[0]
    return head.startswith(_FAILURE_PREFIXES) or "unavailable:" in head


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
    out = truncate_result(out, scratch_dir=ctx.deps.settings.home / "scratch" / "tool-results")
    # Repeated identical failures waste requests; nudge the model off the loop.
    if result_failed(out):
        key = _failure_key(name, args)
        ctx.deps.tool_failures[key] = ctx.deps.tool_failures.get(key, 0) + 1
        count = ctx.deps.tool_failures[key]
        if count == 2:
            out += (
                "\n[hint: this exact call already failed twice — change the approach "
                "or ask the user with clarify instead of repeating it.]"
            )
        elif count >= 3:
            out += "\n[hint: repeated-failure breaker — stop retrying this call.]"
    await ctx.deps.events.on_tool_end(name, out)
    return out


async def maybe_approve(
    ctx: RunContext[TurnDeps],
    tool_name: str,
    summary: str,
    *,
    needs: bool | None = None,
    **args: Any,
) -> str | None:
    if needs is None:
        needs = tool_needs_approval(
            tool_name,
            args=args,
            home=ctx.deps.settings.home,
            workspace=ctx.deps.workspace,
        )
    if not needs:
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
