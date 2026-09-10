"""session_search tool helper."""

from __future__ import annotations

from lattice.session import SessionStore


async def session_search(query: str, store: SessionStore, *, limit: int = 10) -> str:
    rows = await store.search(query, limit=limit)
    if not rows:
        return "(no sessions matched)"
    return "\n".join(
        f"{r['id']} profile={r.get('profile_id')} "
        f"updated={r.get('updated_at')} title={r.get('title')}"
        for r in rows
    )
