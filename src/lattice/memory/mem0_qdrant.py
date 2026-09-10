"""mem0 + local Qdrant memory backend with graceful fallback."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lattice.paths import lattice_home

_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
_EMBED_DIMS = 384

_client_lock = threading.Lock()
_clients: dict[str, Any] = {}
_instances: dict[tuple[str, str], Any] = {}


def _silence_mem0_deps() -> None:
    """Disable mem0 telemetry; skip spaCy (BM25 uses fastembed)."""
    os.environ["MEM0_TELEMETRY"] = "False"
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    try:
        import mem0.memory.telemetry as telemetry

        telemetry.MEM0_TELEMETRY = False
    except Exception:
        pass
    for name in (
        "mem0",
        "mem0.memory",
        "mem0.memory.main",
        "mem0.utils.spacy_models",
        "mem0.utils.lemmatization",
        "mem0.utils.entity_extraction",
        "mem0.vector_stores.qdrant",
        "posthog",
        "qdrant_client",
        "fastembed",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)
    try:
        import mem0.utils.spacy_models as spacy_models

        spacy_models._load_failed_lemma = True
        spacy_models._load_failed_full = True
        spacy_models._nlp_lemma = None
        spacy_models._nlp_full = None
    except Exception:
        pass


@contextmanager
def _quiet_mem0() -> Iterator[None]:
    _silence_mem0_deps()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Payload indexes have no effect in the local Qdrant.*",
        )
        yield


_silence_mem0_deps()


def _shared_qdrant_client(path: Path) -> Any:
    """One embedded Qdrant client per path — local mode allows only a single opener."""
    key = str(path.resolve())
    with _client_lock:
        client = _clients.get(key)
        if client is not None:
            return client
    # Create outside the lock so concurrent waiters don't nest; re-check after.
    from qdrant_client import QdrantClient

    path.mkdir(parents=True, exist_ok=True)
    created = QdrantClient(path=key)
    with _client_lock:
        existing = _clients.get(key)
        if existing is not None:
            created.close()
            return existing
        _clients[key] = created
        return created


def _mem0_config(*, collection: str, path: Path, client: Any) -> dict[str, Any]:
    api_key = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("LATTICE_PROVIDER__API_KEY")
    )
    base_url = (
        os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("LATTICE_PROVIDER__BASE_URL")
        or ("https://openrouter.ai/api/v1" if os.environ.get("OPENROUTER_API_KEY") else None)
    )
    model = (
        os.environ.get("LATTICE_AGENT__MODEL")
        or "gpt-4o-mini"
    )
    llm_config: dict[str, Any] = {"model": model}
    if api_key:
        llm_config["api_key"] = api_key
    if base_url:
        llm_config["openai_base_url"] = base_url

    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": collection,
                "client": client,
                "on_disk": True,
                "embedding_model_dims": _EMBED_DIMS,
            },
        },
        "embedder": {
            "provider": "fastembed",
            "config": {
                "model": _EMBED_MODEL,
                "embedding_dims": _EMBED_DIMS,
            },
        },
        "llm": {
            "provider": "openai",
            "config": llm_config,
        },
        "history_db_path": str(path / "history.db"),
    }


class InMemoryMemory:
    """Fallback when mem0/qdrant are unavailable."""

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
        for msg in reversed(messages):
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                await self.add(msg["content"], metadata={"source": "sync_turn"})
                break


class Mem0QdrantMemory:
    def __init__(self, *, collection: str, path: Path | None = None) -> None:
        self.collection = collection
        self.path = path or (lattice_home() / "qdrant")
        self.path.mkdir(parents=True, exist_ok=True)
        self._fallback = InMemoryMemory(collection)
        self._memory: Any = None
        try:
            with _quiet_mem0():
                from mem0 import Memory

                # mem0 OpenAI client requires a key string at construct time
                if not (
                    os.environ.get("OPENAI_API_KEY")
                    or os.environ.get("OPENROUTER_API_KEY")
                    or os.environ.get("LATTICE_PROVIDER__API_KEY")
                ):
                    os.environ.setdefault("OPENAI_API_KEY", "sk-lattice-memory-placeholder")

                client = _shared_qdrant_client(self.path)
                self._memory = Memory.from_config(
                    _mem0_config(collection=collection, path=self.path, client=client)
                )
        except Exception:
            self._memory = None

    async def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        if self._memory is None:
            return await self._fallback.search(query, limit=limit)
        try:
            with _quiet_mem0():
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
            with _quiet_mem0():
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
            with _quiet_mem0():
                self._memory.update(memory_id, text)
        except Exception:
            await self._fallback.update(memory_id, text)

    async def forget(self, memory_id: str) -> None:
        if self._memory is None:
            await self._fallback.forget(memory_id)
            return
        try:
            with _quiet_mem0():
                self._memory.delete(memory_id)
        except Exception:
            await self._fallback.forget(memory_id)

    async def sync_turn(self, messages: list[dict[str, Any]]) -> None:
        blob = json.dumps(messages[-6:], ensure_ascii=False)[:4000]
        await self.add(blob, metadata={"source": "sync_turn"})


# Back-compat alias during the Chroma → Qdrant switch.
Mem0ChromaMemory = Mem0QdrantMemory


def build_memory(*, collection: str, path: Path | None = None) -> Mem0QdrantMemory:
    root = (path or (lattice_home() / "qdrant")).resolve()
    key = (str(root), collection)
    with _client_lock:
        existing = _instances.get(key)
        if existing is not None:
            return existing
    # Construct outside the lock (Qdrant/fastembed init is slow; avoid deadlock).
    inst = Mem0QdrantMemory(collection=collection, path=root)
    with _client_lock:
        existing = _instances.get(key)
        if existing is not None:
            return existing
        _instances[key] = inst
        return inst
