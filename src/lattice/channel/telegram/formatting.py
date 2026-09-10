"""Telegram HTML escape and message chunking."""

from __future__ import annotations

import html


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def chunk_text(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks
