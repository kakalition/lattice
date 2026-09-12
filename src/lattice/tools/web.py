"""Web search (Tavily) and fetch (httpx)."""

from __future__ import annotations

import re
from typing import Any

import httpx


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fence_untrusted(label: str, body: str) -> str:
    return f'<untrusted source="{label}">\n{body}\n</untrusted>'


async def web_search(query: str, *, api_key: str | None, max_results: int = 5) -> str:
    if not api_key:
        return "error: web_search unavailable: set tavily_api_key / LATTICE_TAVILY_API_KEY"
    payload = {"api_key": api_key, "query": query, "max_results": max_results}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://api.tavily.com/search", json=payload)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    lines: list[str] = []
    for item in data.get("results") or []:
        lines.append(f"- {item.get('title')}: {item.get('url')}\n  {item.get('content')}")
    return fence_untrusted("tavily", "\n".join(lines) or "(no results)")


async def web_fetch(url: str, *, max_chars: int = 20_000) -> str:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        text = _strip_html(resp.text)[:max_chars]
    return fence_untrusted(url, text)
