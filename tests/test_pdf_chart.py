"""PDF and chart generation tool tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lattice.deps import CORE_TOOL_NAMES
from lattice.tools.chart import generate_chart
from lattice.tools.groups import tool_functions
from lattice.tools.pdf import generate_pdf
from lattice.tools.theme import LIGHT, oklch_to_hex


def test_oklch_to_hex_stable() -> None:
    assert oklch_to_hex(1.0, 0.0, 0.0) == "#FFFFFF"
    assert LIGHT.charts[0].startswith("#")
    assert len(LIGHT.charts[0]) == 7


@pytest.mark.asyncio
async def test_generate_pdf_markdown_and_json(tmp_path: Path) -> None:
    out = await generate_pdf(
        "exports/report.pdf",
        "Quarterly Review",
        "# Highlights\nShip landed.\n\n## Notes\n- Revenue up\n- Costs flat\n",
        subtitle="Q1 summary",
        workspace=tmp_path,
        home=tmp_path,
    )
    assert out.startswith("wrote pdf")
    pdf = tmp_path / "exports" / "report.pdf"
    assert pdf.is_file() and pdf.stat().st_size > 500

    blocks = json.dumps(
        [
            {"type": "h2", "text": "Totals"},
            {
                "type": "table",
                "headers": ["Metric", "Value"],
                "rows": [["Users", "1,240"], ["MRR", "$18k"]],
            },
            {"type": "callout", "text": "Keep burn under $40k."},
        ]
    )
    out2 = await generate_pdf(
        "exports/blocks.pdf",
        "Ops Brief",
        blocks,
        workspace=tmp_path,
        home=tmp_path,
    )
    assert out2.startswith("wrote pdf")
    assert (tmp_path / "exports" / "blocks.pdf").is_file()


@pytest.mark.asyncio
async def test_generate_chart_bar_and_donut(tmp_path: Path) -> None:
    data = {
        "labels": ["Jan", "Feb", "Mar"],
        "series": [
            {"name": "Desktop", "values": [120, 180, 160]},
            {"name": "Mobile", "values": [80, 140, 190]},
        ],
    }
    out = await generate_chart(
        "exports/traffic.png",
        "bar",
        "Traffic",
        json.dumps(data),
        description="Visitors by channel",
        theme="light",
        workspace=tmp_path,
        home=tmp_path,
    )
    assert out.startswith("wrote chart")
    png = tmp_path / "exports" / "traffic.png"
    assert png.is_file() and png.stat().st_size > 1000

    pie = {
        "labels": ["A", "B", "C"],
        "series": [{"name": "Share", "values": [40, 35, 25]}],
    }
    out2 = await generate_chart(
        "exports/share.png",
        "donut",
        "Share",
        json.dumps(pie),
        theme="dark",
        workspace=tmp_path,
        home=tmp_path,
    )
    assert out2.startswith("wrote chart")
    assert (tmp_path / "exports" / "share.png").is_file()


def test_core_tools_include_pdf_chart() -> None:
    assert "media/pdf" in CORE_TOOL_NAMES
    assert "media/chart" in CORE_TOOL_NAMES


def test_build_toolsets_includes_new_tools() -> None:

    mapping = tool_functions()
    assert "media/pdf" in mapping
    assert "media/chart" in mapping
    assert set(CORE_TOOL_NAMES) <= set(mapping)
