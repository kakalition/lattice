"""mem0 + Chroma memory backend with graceful fallback."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from lattice.paths import lattice_home


class InMemoryMemory:
    """Fallback when mem0/chroma are unavailable."""

    def __init__(self, collection: str = "lattice-default") -> None:
        self.collection = collection
        self._items: dict[str, dict[str, Any]] = {}

    async def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        q = query.lower()
        hits = [v for v in self._items.values() if q in v["text"].lower()]
        return hits[:limit]

    async def add(self, text: str, *, metadata: dict[str, Any] | None = None) -> str:
        mid = str(uuid.uuid4())
        self._items[mid] = {"id": mid, "text": text, "metadata": metadata or {}}
        return mid

    async def update(self, memory_id: str, text: str) -> None:
        if memory_id in self._items:
            self._items[memory_id]["text"] = text

    async def forget(self, memory_id: str) -> None:
        self._items.pop(memory_id, None)

    async def sync_turn(self, messages: list[dict[str, Any]]) -> None:
        # Lightweight: store last user message as memory candidate
        for msg in reversed(messages):
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                await self.add(msg["content"], metadata={"source": "sync_turn"})
                break


class Mem0ChromaMemory:
    def __init__(self, *, collection: str, path: Path | None = None) -> None:
        self.collection = collection
        self.path = path or (lattice_home() / "chroma")
        self.path.mkdir(parents=True, exist_ok=True)
        self._fallback = InMemoryMemory(collection)
        self._memory: Any = None
        try:
            from mem0 import Memory

            config = {
                "vector_store": {
                    "provider": "chroma",
                    "config": {
                        "path": str(self.path),
                        "collection_name": collection,
                    },
                }
            }
            self._memory = Memory.from_config(config)
        except Exception:
            self._memory = None

    async def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        if self._memory is None:
            return await self._fallback.search(query, limit=limit)
        try:
            results = self._memory.search(query, user_id=self.collection, limit=limit)
            if isinstance(results, dict):
                results = results.get("results") or results.get("memories") or []
            out: list[dict[str, Any]] = []
            for item in results or []:
                if isinstance(item, dict):
                    out.append(
                        {
                            "id": str(item.get("id") or item.get("memory_id") or ""),
                            "text": str(item.get("memory") or item.get("text") or item),
                            "metadata": item.get("metadata") or {},
                        }
                    )
            return out
        except Exception:
            return await self._fallback.search(query, limit=limit)

    async def add(self, text: str, *, metadata: dict[str, Any] | None = None) -> str:
        if self._memory is None:
            return await self._fallback.add(text, metadata=metadata)
        try:
            result = self._memory.add(text, user_id=self.collection, metadata=metadata or {})
            if isinstance(result, dict):
                return str(result.get("id") or result.get("memory_id") or uuid.uuid4())
            return str(uuid.uuid4())
        except Exception:
            return await self._fallback.add(text, metadata=metadata)

    async def update(self, memory_id: str, text: str) -> None:
        if self._memory is None:
            await self._fallback.update(memory_id, text)
            return
        try:
            self._memory.update(memory_id, text)
        except Exception:
            await self._fallback.update(memory_id, text)

    async def forget(self, memory_id: str) -> None:
        if self._memory is None:
            await self._fallback.forget(memory_id)
            return
        try:
            self._memory.delete(memory_id)
        except Exception:
            await self._fallback.forget(memory_id)

    async def sync_turn(self, messages: list[dict[str, Any]]) -> None:
        blob = json.dumps(messages[-6:], ensure_ascii=False)[:4000]
        await self.add(blob, metadata={"source": "sync_turn"})


def build_memory(*, collection: str, path: Path | None = None) -> Mem0ChromaMemory:
    return Mem0ChromaMemory(collection=collection, path=path)
