"""Agent tool: generate_chart."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.chart import generate_chart


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def generate_chart_tool(
        ctx: RunContext[TurnDeps],
        path: str,
        chart_type: str,
        title: str,
        data_json: str,
        description: str = "",
        theme: str = "light",
    ) -> str:
        """Create a shadcn-styled chart (bar|line|area|pie|donut) as png/svg/pdf.

        ``data_json`` example:
        {"labels":["Jan","Feb"],"series":[{"name":"Desktop","values":[12,18]},
         {"name":"Mobile","values":[8,14]}]}
        For pie/donut, one series of values matching labels (or one value per series).
        """
        if err := not_allowed(ctx, "generate_chart"):
            return err

        async def _op() -> str:
            out = await generate_chart(
                path,
                chart_type,
                title,
                data_json,
                description=description,
                theme=theme,
                workspace=ctx.deps.workspace,
                home=ctx.deps.settings.home,
            )
            if out.startswith("wrote chart"):
                from lattice.tools.file_safety import resolve_agent_path

                try:
                    target = resolve_agent_path(path, ctx.deps.workspace, home=ctx.deps.settings.home)
                    if target.suffix.lower() not in {".png", ".svg", ".pdf"}:
                        target = target.with_suffix(".png")
                    ctx.deps.outbound_media.append(Path(target))
                except Exception:
                    pass
            return out

        return await traced(
            ctx,
            "generate_chart",
            {"path": path, "chart_type": chart_type, "title": title, "theme": theme},
            _op,
        )

    return {"generate_chart": generate_chart_tool}
