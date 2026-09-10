"""Memory tool helpers (wired into agent deps)."""

from __future__ import annotations

from typing import Any

from lattice.memory.base import Memory


async def memory_search(memory: Memory, query: str) -> str:
    hits = await memory.search(query)
    if not hits:
        return "(no memories)"
    return "\n".join(f"{h.get('id')}: {h.get('text')}" for h in hits)


async def memory_add(memory: Memory, text: str) -> str:
    mid = await memory.add(text)
    return f"added memory {mid}"


async def memory_update(memory: Memory, memory_id: str, text: str) -> str:
    await memory.update(memory_id, text)
    return f"updated memory {memory_id}"


async def memory_forget(memory: Memory, memory_id: str) -> str:
    await memory.forget(memory_id)
    return f"forgot memory {memory_id}"


def format_prefetch(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return ""
    lines = ["Relevant memories:"]
    for h in hits:
        lines.append(f"- {h.get('text')}")
    return "\n".join(lines)
