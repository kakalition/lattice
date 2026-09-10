"""Agent tool: schedule_add."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.scheduler.tools import schedule_add
from lattice.tools.agent._common import AgentT, not_allowed


def register(agent: AgentT) -> dict[str, Any]:
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
        if err := not_allowed(ctx, "schedule_add"):
            return err
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

    return {"schedule_add": schedule_add_tool}
