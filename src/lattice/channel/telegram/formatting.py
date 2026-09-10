"""Telegram HTML escape, light markdown → HTML, and message chunking."""

from __future__ import annotations

import html
import re

_CODE_FENCE = re.compile(r"```(?:[a-zA-Z0-9_+-]*\n)?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def markdown_to_telegram_html(text: str) -> str:
    """Convert common Markdown emphasis to Telegram HTML; escape everything else.

    Handles fenced/inline code, links, ``**bold**``, and ``*italic*``.
    Snake_case underscores are left alone (no ``_italic_``).
    """
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
