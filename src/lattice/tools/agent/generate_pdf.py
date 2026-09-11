"""Agent tool: generate_pdf."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, traced
from lattice.tools.agent._common import AgentT, not_allowed
from lattice.tools.pdf import generate_pdf


def register(agent: AgentT) -> dict[str, Any]:
    @agent.tool
    async def generate_pdf_tool(
        ctx: RunContext[TurnDeps],
        path: str,
        title: str,
        content: str = "",
        subtitle: str = "",
    ) -> str:
        """Create an aesthetic PDF under the workspace.

        ``content`` may be markdown (# ##, paragraphs, - bullets) or a JSON list of
        blocks: h1/h2/p/bullets/table/callout/hr/spacer.
        Example table block:
        {"type":"table","headers":["A","B"],"rows":[["1","2"]]}
        """
        if err := not_allowed(ctx, "generate_pdf"):
            return err

        async def _op() -> str:
            out = await generate_pdf(
                path,
                title,
                content,
                subtitle=subtitle,
                workspace=ctx.deps.workspace,
                home=ctx.deps.settings.home,
            )
            if out.startswith("wrote pdf"):
                from lattice.tools.file_safety import resolve_agent_path

                try:
                    target = resolve_agent_path(path, ctx.deps.workspace, home=ctx.deps.settings.home)
                    if target.suffix.lower() != ".pdf":
                        target = target.with_suffix(".pdf")
                    ctx.deps.outbound_media.append(Path(target))
                except Exception:
                    pass
            return out

        return await traced(
            ctx,
            "generate_pdf",
            {"path": path, "title": title, "subtitle": subtitle},
            _op,
        )

    return {"generate_pdf": generate_pdf_tool}
