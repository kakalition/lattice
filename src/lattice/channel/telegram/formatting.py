"""Telegram HTML escape, light markdown → HTML, and message chunking."""

from __future__ import annotations

import html
import re

_CODE_FENCE = re.compile(r"```(?:[a-zA-Z0-9_+-]*\n)?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
# ATX heading: 1-6 leading '#', a space, then text (optional closing '#'s).
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")
_TABLE_SEP = re.compile(r"^\s*\|?[\s\-:|]+\|[\s\-:|]*\|?\s*$")


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def markdown_tables_to_lists(text: str) -> str:
    """Rewrite pipe tables into labeled bullets (Telegram-friendly)."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            "|" in line
            and i + 1 < len(lines)
            and _TABLE_SEP.match(lines[i + 1])
            and not line.strip().startswith("```")
        ):
            headers = _split_row(line)
            i += 2  # skip header + separator
            while i < len(lines) and "|" in lines[i] and not _TABLE_SEP.match(lines[i]):
                cells = _split_row(lines[i])
                parts: list[str] = []
                for h, c in zip(headers, cells, strict=False):
                    if not c or c == "-":
                        continue
                    label = h or "field"
                    parts.append(f"**{label}** — {c}" if label else c)
                if parts:
                    out.append("- " + "; ".join(parts))
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def markdown_to_telegram_html(text: str) -> str:
    """Convert common Markdown emphasis to Telegram HTML; escape everything else.

    Handles fenced/inline code, links, ATX headings (Telegram has no heading tag,
    so they become bold), ``**bold**``, and ``*italic*``. Pipe tables are
    rewritten to bullets first. Snake_case underscores stay plain.
    """
    text = markdown_tables_to_lists(text)
    held: list[str] = []

    def hold(fragment: str) -> str:
        held.append(fragment)
        return f"\x00{len(held) - 1}\x00"

    def restore(s: str) -> str:
        return _PLACEHOLDER.sub(lambda m: held[int(m.group(1))], s)

    def fence(m: re.Match[str]) -> str:
        return hold(f"<pre>{html.escape(m.group(1).strip())}</pre>")

    def inline(m: re.Match[str]) -> str:
        return hold(f"<code>{html.escape(m.group(1))}</code>")

    def link(m: re.Match[str]) -> str:
        label = html.escape(m.group(1), quote=False)
        url = html.escape(m.group(2), quote=True)
        return hold(f'<a href="{url}">{label}</a>')

    out = _CODE_FENCE.sub(fence, text)
    out = _INLINE_CODE.sub(inline, out)
    out = _LINK.sub(link, out)
    out = _HEADING.sub(r"**\1**", out)
    out = html.escape(out, quote=False)
    out = _BOLD.sub(r"<b>\1</b>", out)
    out = _ITALIC.sub(r"<i>\1</i>", out)
    return restore(out)


def chunk_text(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks
