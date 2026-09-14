"""Built-in ``media`` group — OCR, PDF, and chart generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.tools.chart import generate_chart as _generate_chart
from lattice.tools.groups._common import ToolBinding, ToolGroup, ToolsetT
from lattice.tools.ocr import ocr_image
from lattice.tools.pdf import generate_pdf as _generate_pdf

_GROUP = "media"


def register_ocr(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def ocr(ctx: RunContext[TurnDeps], path: str) -> str:
        """Extract text from an image (png/jpg/webp/…). Use paths from [media] or workspace."""
        return await ocr_image(path, workspace=ctx.deps.workspace, home=ctx.deps.settings.home)

    return {"ocr": ocr}


def register_pdf(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def pdf(
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
        out = await _generate_pdf(
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

    return {"pdf": pdf}


def register_chart(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def chart(
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
        out = await _generate_chart(
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

    return {"chart": chart}


GROUP = ToolGroup(
    name=_GROUP,
    description="Media: OCR images, and generate PDFs or charts.",
    bindings=(
        ToolBinding("ocr", ToolTier.EAGER, register_ocr),
        ToolBinding("pdf", ToolTier.COLD, register_pdf),
        ToolBinding("chart", ToolTier.COLD, register_chart),
    ),
)
