"""Built-in ``schedule`` group — timed reminders and timezone."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.scheduler.tools import schedule_add as _schedule_add
from lattice.scheduler.tools import schedule_cancel as _schedule_cancel
from lattice.scheduler.tools import schedule_list as _schedule_list
from lattice.scheduler.tools import timezone_get as _timezone_get
from lattice.scheduler.tools import timezone_set as _timezone_set
from lattice.tools.groups._common import ToolBinding, ToolGroup, ToolsetT

_GROUP = "schedule"


def register_add(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def add(
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
        Prefer this over todo for anything time-based. deliver=telegram|cli|none.
        `reminder` is delivered verbatim at fire time — write the final, warm message
        (see the `reminder` skill), not a bare note-to-self."""
        tz = timezone or ctx.deps.settings.timezone
        return _schedule_add(
            reminder=reminder,
            home=ctx.deps.settings.home,
            run_at=run_at,
            cron=cron,
            timezone=tz,
            deliver=deliver,
            profile=ctx.deps.profile.id,
            job_id=job_id,
        )

    return {"add": add}


def register_list(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def list(ctx: RunContext[TurnDeps]) -> str:
        """List scheduled reminder jobs."""
        return _schedule_list(home=ctx.deps.settings.home)

    return {"list": list}


def register_cancel(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def cancel(ctx: RunContext[TurnDeps], job_id: str) -> str:
        """Cancel a scheduled job by id."""
        return _schedule_cancel(job_id, home=ctx.deps.settings.home)

    return {"cancel": cancel}


def register_timezone_get(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def timezone_get(ctx: RunContext[TurnDeps]) -> str:
        """Show the remembered IANA timezone used for reminders."""
        return _timezone_get(home=ctx.deps.settings.home)

    return {"timezone_get": timezone_get}


def register_timezone_set(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def timezone_set(ctx: RunContext[TurnDeps], timezone: str) -> str:
        """Persist the user's IANA timezone (e.g. Asia/Ho_Chi_Minh) for all reminders."""
        result = _timezone_set(timezone, home=ctx.deps.settings.home)
        if result.startswith("timezone saved:"):
            ctx.deps.settings.timezone = timezone.strip()
        return result

    return {"timezone_set": timezone_set}


GROUP = ToolGroup(
    name=_GROUP,
    description="Schedule: timed reminders, job listing/cancel, and timezone.",
    bindings=(
        ToolBinding("add", ToolTier.EAGER, register_add),
        ToolBinding("list", ToolTier.COLD, register_list),
        ToolBinding("cancel", ToolTier.COLD, register_cancel),
        ToolBinding("timezone_get", ToolTier.COLD, register_timezone_get),
        ToolBinding("timezone_set", ToolTier.COLD, register_timezone_set),
    ),
)
