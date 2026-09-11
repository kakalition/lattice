"""Aesthetic PDF generation (reportlab) with shadcn-inspired chrome."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from lattice.tools.file_safety import PathDeniedError, resolve_agent_path
from lattice.tools.theme import LIGHT, hex_to_rgb255

_BLOCK_TYPES = frozenset({"h1", "h2", "p", "bullets", "table", "callout", "hr", "spacer"})


def _parse_blocks(content: str) -> list[dict[str, Any]]:
    raw = (content or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [b for b in data if isinstance(b, dict)]
        except json.JSONDecodeError:
            pass
    # Markdown-ish fallback
    blocks: list[dict[str, Any]] = []
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("# "):
            blocks.append({"type": "h1", "text": line[2:].strip()})
            i += 1
            continue
        if line.startswith("## "):
            blocks.append({"type": "h2", "text": line[3:].strip()})
            i += 1
            continue
        if line.startswith("- ") or line.startswith("* "):
            items: list[str] = []
            while i < len(lines) and (lines[i].startswith("- ") or lines[i].startswith("* ")):
                items.append(lines[i][2:].strip())
                i += 1
            blocks.append({"type": "bullets", "items": items})
            continue
        if set(line.strip()) <= {"-", "—", "–", "="} and len(line.strip()) >= 3:
            blocks.append({"type": "hr"})
            i += 1
            continue
        para: list[str] = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(("# ", "## ", "- ", "* ")):
            para.append(lines[i].strip())
            i += 1
        blocks.append({"type": "p", "text": " ".join(para)})
    return blocks


def _safe_filename(path: Path) -> Path:
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")
    return path


def _build_pdf(
    target: Path,
    *,
    title: str,
    subtitle: str,
    blocks: list[dict[str, Any]],
) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        KeepTogether,
        ListFlowable,
        ListItem,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    fg = colors.Color(*[c / 255 for c in hex_to_rgb255(LIGHT.foreground)])
    muted = colors.Color(*[c / 255 for c in hex_to_rgb255(LIGHT.muted_foreground)])
    border = colors.Color(*[c / 255 for c in hex_to_rgb255(LIGHT.border)])
    accent = colors.Color(*[c / 255 for c in hex_to_rgb255(LIGHT.charts[0])])
    muted_bg = colors.Color(*[c / 255 for c in hex_to_rgb255(LIGHT.muted)])
    card_fg = fg

    target.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(target),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=title or "Lattice",
        author="Lattice",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "LatticeTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=26,
        leading=32,
        textColor=fg,
        spaceAfter=4,
        alignment=TA_LEFT,
    )
    subtitle_style = ParagraphStyle(
        "LatticeSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=15,
        textColor=muted,
        spaceAfter=16,
    )
    h1_style = ParagraphStyle(
        "LatticeH1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=fg,
        spaceBefore=14,
        spaceAfter=8,
    )
    h2_style = ParagraphStyle(
        "LatticeH2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12.5,
        leading=16,
        textColor=fg,
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "LatticeBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=15,
        textColor=card_fg,
        spaceAfter=8,
    )
    bullet_style = ParagraphStyle(
        "LatticeBullet",
        parent=body_style,
        leftIndent=0,
        spaceAfter=2,
    )
    callout_style = ParagraphStyle(
        "LatticeCallout",
        parent=body_style,
        textColor=fg,
        leading=14,
    )

    story: list[Any] = []

    # Accent bar via a thin colored table
    bar = Table([[""]], colWidths=[doc.width], rowHeights=[3])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent)]))
    story.append(bar)
    story.append(Spacer(1, 14))

    if title.strip():
        story.append(Paragraph(_esc(title.strip()), title_style))
    if subtitle.strip():
        story.append(Paragraph(_esc(subtitle.strip()), subtitle_style))
    else:
        story.append(Spacer(1, 8))

    for block in blocks:
        kind = str(block.get("type") or "p").lower()
        if kind not in _BLOCK_TYPES:
            kind = "p"
        if kind == "h1":
            story.append(Paragraph(_esc(str(block.get("text") or "")), h1_style))
            story.append(
                HRFlowable(width="100%", thickness=0.6, color=border, spaceBefore=0, spaceAfter=8)
            )
        elif kind == "h2":
            story.append(Paragraph(_esc(str(block.get("text") or "")), h2_style))
        elif kind == "p":
            text = str(block.get("text") or "").strip()
            if text:
                story.append(Paragraph(_esc(text), body_style))
        elif kind == "bullets":
            items = block.get("items") or []
            if isinstance(items, list) and items:
                flow = ListFlowable(
                    [
                        ListItem(Paragraph(_esc(str(it)), bullet_style), leftIndent=8, bulletColor=accent)
                        for it in items
                    ],
                    bulletType="bullet",
                    start="•",
                    leftIndent=14,
                    bulletFontSize=9,
                    spaceBefore=2,
                    spaceAfter=8,
                )
                story.append(flow)
        elif kind == "table":
            headers = [str(h) for h in (block.get("headers") or [])]
            rows = block.get("rows") or []
            if headers or rows:
                data = []
                if headers:
                    data.append([Paragraph(f"<b>{_esc(h)}</b>", body_style) for h in headers])
                for row in rows:
                    if not isinstance(row, (list, tuple)):
                        continue
                    data.append([Paragraph(_esc(str(c)), body_style) for c in row])
                if data:
                    cols = max(len(r) for r in data)
                    col_w = doc.width / cols
                    tbl = Table(data, colWidths=[col_w] * cols, hAlign="LEFT")
                    style_cmds = [
                        ("BACKGROUND", (0, 0), (-1, 0), muted_bg) if headers else ("BACKGROUND", (0, 0), (-1, 0), colors.white),
                        ("TEXTCOLOR", (0, 0), (-1, -1), fg),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("BOX", (0, 0), (-1, -1), 0.6, border),
                        ("INNERGRID", (0, 0), (-1, -1), 0.4, border),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                    tbl.setStyle(TableStyle(style_cmds))
                    story.append(KeepTogether([tbl, Spacer(1, 10)]))
        elif kind == "callout":
            text = str(block.get("text") or "").strip()
            if text:
                inner = Table(
                    [[Paragraph(_esc(text), callout_style)]],
                    colWidths=[doc.width],
                )
                inner.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), muted_bg),
                            ("BOX", (0, 0), (-1, -1), 0.6, border),
                            ("LEFTPADDING", (0, 0), (-1, -1), 12),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                            ("TOPPADDING", (0, 0), (-1, -1), 10),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                            ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
                        ]
                    )
                )
                story.append(inner)
                story.append(Spacer(1, 10))
        elif kind == "hr":
            story.append(
                HRFlowable(width="100%", thickness=0.6, color=border, spaceBefore=6, spaceAfter=10)
            )
        elif kind == "spacer":
            story.append(Spacer(1, float(block.get("height") or 12)))

    def _footer(canvas: Any, _doc: Any) -> None:
        canvas.saveState()
        # Close but not touching: ~3.5mm (~10pt) between rule and 8pt label ascent.
        canvas.setStrokeColor(colors.Color(0.62, 0.62, 0.62))
        canvas.setLineWidth(0.6)
        text_y = 10 * mm
        line_y = text_y + 4.5 * mm
        canvas.line(18 * mm, line_y, A4[0] - 18 * mm, line_y)
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(18 * mm, text_y, "Lattice")
        canvas.drawRightString(A4[0] - 18 * mm, text_y, f"{canvas.getPageNumber()}")
        canvas.restoreState()

    if not story:
        story.append(Paragraph(_esc(title or "Empty document"), body_style))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


async def generate_pdf(
    path: str,
    title: str,
    content: str = "",
    *,
    subtitle: str = "",
    workspace: Path,
    home: Path,
) -> str:
    """Write an aesthetic PDF under the workspace. ``content`` is JSON blocks or markdown."""
    try:
        target = _safe_filename(resolve_agent_path(path, workspace, home=home))
    except PathDeniedError as exc:
        return f"error: {exc}"

    blocks = _parse_blocks(content)

    def _run() -> str:
        try:
            _build_pdf(target, title=title, subtitle=subtitle, blocks=blocks)
        except Exception as exc:  # noqa: BLE001
            return f"pdf error: {exc}"
        rel = _display_path(target, workspace)
        return f"wrote pdf → {rel} ({target.stat().st_size} bytes)"

    return await asyncio.to_thread(_run)


def _display_path(target: Path, workspace: Path) -> str:
    try:
        return str(target.relative_to(workspace.resolve()))
    except ValueError:
        return str(target)
