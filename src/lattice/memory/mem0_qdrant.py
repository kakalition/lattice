"""mem0 + local Qdrant memory backend with graceful fallback."""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import threading
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from lattice.paths import lattice_home

logger = logging.getLogger("lattice.memory")

_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
_EMBED_DIMS = 384

# mem0 asks its LLM for a JSON fact list and hard-fails the whole batch on any
# syntax drift (prose, markdown fences, trailing commas). Passed as mem0's
# ``custom_instructions`` to steer weaker models back to strict JSON.
_STRICT_JSON_INSTRUCTIONS = (
    'Respond with ONLY a JSON object: {"memory": [{"id": "0", "text": "...", '
    '"attributed_to": "user" or "assistant"}]}. No prose, no markdown fences, no '
    "trailing commas, no comments; escape quotes and newlines. If nothing is worth "
    'remembering, return {"memory": []}.'
)

_client_lock = threading.Lock()
_clients: dict[str, Any] = {}
_instances: dict[tuple[str, str], Any] = {}
_clients_closed = False


def close_memory() -> None:
    """Release embedded Qdrant clients while the interpreter is still usable.

    ``QdrantClient.__del__`` calls ``close()``, which lazily imports
    ``portalocker`` and closes per-collection storage. If that runs during
    interpreter finalisation it raises ``ImportError: sys.meta_path is None``
    and libc++ aborts the process (``recursive_mutex lock failed``).

    Closing explicitly at exit avoids that, and also releases the on-disk lock
    so a following process can open the same store. Safe to call more than once.
    """
    global _clients_closed
    with _client_lock:
        if _clients_closed:
            return
        _clients_closed = True
        instances = list(_instances.values())
        clients = list(_clients.values())
        _instances.clear()
        _clients.clear()
    # Tear mem0 down first so it stops holding the vector store, then close the
    # shared clients. Best-effort: shutdown must never raise.
    for instance in instances:
        with suppress(Exception):
            instance.close()
    for client in clients:
        with suppress(Exception):
            client.close()


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
        # A client created after a shutdown pass must be closed at exit too.
        global _clients_closed
        _clients_closed = False
        return created


def _mem0_config(
    *,
    collection: str,
    path: Path,
    client: Any,
    llm_model: str | None = None,
    is_reasoning_model: bool | None = None,
) -> dict[str, Any]:
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
    # mem0 calls an LLM to extract facts; without an explicit model it hardcodes
    # gpt-4o-mini, which silently bills OpenAI even when lattice.yaml configures
    # a different provider. Callers pass the resolved primary model.
    model = llm_model or os.environ.get("LATTICE_AGENT__MODEL") or "gpt-4o-mini"
    llm_config: dict[str, Any] = {"model": model}
    if api_key:
        llm_config["api_key"] = api_key
    if base_url:
        llm_config["openai_base_url"] = base_url
    if is_reasoning_model is not None:
        # Drops max_tokens/temperature for reasoning models, which otherwise
        # truncate their JSON output and silently extract nothing.
        llm_config["is_reasoning_model"] = is_reasoning_model

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
        "custom_instructions": _STRICT_JSON_INSTRUCTIONS,
        "history_db_path": str(path / "history.db"),
    }


def _first_memory_id(result: Any) -> str | None:
    """Pull the first stored memory id out of a mem0 ``add`` return value.

    mem0 wraps results differently per call shape: ``{"results": [...]}`` when
    it infers, a bare list when ``infer=False``, and occasionally a flat dict.
    Reading only ``result["id"]`` silently missed all of these and made ``add``
    hand back a fabricated uuid — so a follow-up update/forget hit nothing.
    """
    if isinstance(result, dict):
        if result.get("results"):
            return _first_memory_id(result["results"])
        mid = result.get("id") or result.get("memory_id")
        return str(mid) if mid else None
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                mid = item.get("id") or item.get("memory_id")
                if mid:
                    return str(mid)
    return None


class InMemoryMemory:
    """Fallback when mem0/qdrant are unavailable."""

    def __init__(self, collection: str = "lattice-default") -> None:
        self.collection = collection
        self._items: dict[str, dict[str, Any]] = {}

    async def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        q = query.lower()
        hits = [v for v in self._items.values() if q in v["text"].lower()]
        return hits[:limit]

    async def add(
        self, text: str, *, metadata: dict[str, Any] | None = None, infer: bool = True
    ) -> str:
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
    def __init__(
        self,
        *,
        collection: str,
        path: Path | None = None,
        llm_model: str | None = None,
        is_reasoning_model: bool | None = None,
        extract_on_turn: bool = False,
    ) -> None:
        self.collection = collection
        self.path = path or (lattice_home() / "qdrant")
        self.path.mkdir(parents=True, exist_ok=True)
        self._extract_on_turn = extract_on_turn
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
                    _mem0_config(
                        collection=collection,
                        path=self.path,
                        client=client,
                        llm_model=llm_model,
                        is_reasoning_model=is_reasoning_model,
                    )
                )
        except Exception:
            self._memory = None
            logger.warning(
                "mem0 backend unavailable; memory will use the in-memory fallback", exc_info=True
            )

    async def search(
        self, query: str, *, limit: int = 5, include_probes: bool = False
    ) -> list[dict[str, Any]]:
        if self._memory is None:
            return await self._fallback.search(query, limit=limit)
        try:
            with _quiet_mem0():
                # mem0 v2 rejects top-level entity kwargs on search(); they must
                # be passed as filters. Passing user_id= here raised ValueError,
                # which the except below turned into a silent empty result.
                results = self._memory.search(
                    query, filters={"user_id": self.collection}, limit=limit
                )
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
            # Probe rows are internal health-check artefacts; never surface them.
            # They are also deleted by probe_memory, but a crash mid-probe could
            # leave one behind, and it must not leak into the model's context.
            if include_probes:
                return out
            return [m for m in out if not str(m["text"]).startswith(_PROBE_PREFIX)]
        except Exception:
            logger.warning(
                "mem0 search failed; returning empty results from the fallback", exc_info=True
            )
            return await self._fallback.search(query, limit=limit)

    async def add(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        probe_text: str | None = None,
        infer: bool = True,
    ) -> str:
        """Store ``text``; return its id.

        ``infer=False`` embeds and stores the text verbatim without an extraction
        LLM call. ``probe_text`` is a self-check hook: when set, it is stored
        verbatim with inference disabled, so a health check can write an exact
        string and then assert that search returns it — without paying for an
        extraction LLM call.
        """
        if self._memory is None:
            return await self._fallback.add(text, metadata=metadata)
        try:
            with _quiet_mem0():
                if probe_text is not None:
                    result = self._memory.add(
                        probe_text,
                        user_id=self.collection,
                        metadata=metadata or {},
                        infer=False,
                    )
                else:
                    result = self._memory.add(
                        text, user_id=self.collection, metadata=metadata or {}, infer=infer
                    )
            return str(_first_memory_id(result) or uuid.uuid4())
        except Exception:
            logger.warning("mem0 add failed; storing in the in-memory fallback", exc_info=True)
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
        # Send a readable transcript, not a ``json.dumps`` blob: feeding the
        # extractor JSON-shaped text invites JSON-shaped (and malformed) replies,
        # and truncating a JSON blob mid-structure adds noise.
        lines: list[str] = []
        for msg in messages[-6:]:
            content = msg.get("content")
            role = str(msg.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            label = "User" if role == "user" else "Assistant"
            lines.append(f"{label}: {content.strip()[:600]}")
        if lines:
            await self.add(
                "\n".join(lines),
                metadata={"source": "sync_turn"},
                infer=self._extract_on_turn,
            )

    def close(self) -> None:
        """Best-effort teardown of the mem0 instance (vector store client is shared).

        The underlying Qdrant client is owned by ``_shared_qdrant_client`` and is
        released in ``close_memory``, so this only drops our reference to mem0.
        """
        self._memory = None


# Back-compat alias during the Chroma → Qdrant switch.
Mem0ChromaMemory = Mem0QdrantMemory


def build_memory(
    *,
    collection: str,
    path: Path | None = None,
    llm_model: str | None = None,
    is_reasoning_model: bool | None = None,
    extract_on_turn: bool = False,
) -> Mem0QdrantMemory:
    root = (path or (lattice_home() / "qdrant")).resolve()
    # Cache on the model as well: mem0 bakes it into the instance, so a profile
    # with a different model must not receive the wrong backend.
    key = (str(root), collection, llm_model or "", extract_on_turn)
    with _client_lock:
        existing = _instances.get(key)
        if existing is not None:
            return existing
    # Construct outside the lock (Qdrant/fastembed init is slow; avoid deadlock).
    inst = Mem0QdrantMemory(
        collection=collection,
        path=root,
        llm_model=llm_model,
        is_reasoning_model=is_reasoning_model,
        extract_on_turn=extract_on_turn,
    )
    with _client_lock:
        existing = _instances.get(key)
        if existing is not None:
            return existing
        _instances[key] = inst
        return inst
        _instances[key] = inst
        return inst


# A nonsense token: unique per run so repeated probes cannot match each other,
# and lexically unlikely to collide with real user memories.
_PROBE_PREFIX = "lattice-probe"


def probe_memory(
    memory: Mem0QdrantMemory,
    *,
    collection: str | None = None,
) -> None:
    """Assert the memory round-trip works; raise ``RuntimeError`` if it does not.

    Writes a unique token via the same code path the tools use, then searches for
    it. A break in ``add``/``search`` — a stale mem0 call signature, a mis-scoped
    filter, a dead backend — fails here loudly at boot instead of silently
    returning zero hits for the rest of the session.

    The write uses ``infer=False``, so it costs one embedding and no LLM call.
    The probe row is deleted afterwards; it never reaches the extraction
    pipeline, is filtered out of normal search results, and uses a zero vector
    so it is never a semantic near-neighbour.
    """
    token = f"{_PROBE_PREFIX}-{uuid.uuid4().hex}"
    scope = collection or memory.collection

    # A working fallback still means memories evaporate at exit and are invisible
    # to future turns, so treat an unavailable backend as a failed check rather
    # than letting the round-trip pass against in-memory storage.
    if memory._memory is None:
        raise RuntimeError(
            "memory self-check FAILED: the mem0/Qdrant backend is unavailable and the "
            "in-memory fallback is active, so memories will not persist across turns "
            "(see the 'mem0 backend unavailable' warning for the underlying error)"
        )

    async def _round_trip() -> str | None:
        await memory.add(token, metadata={"source": "self_check"}, probe_text=token)
        hits = await memory.search(token, limit=15, include_probes=True)
        found = next((h for h in hits if token in str(h.get("text") or "")), None)
        if found is not None:
            try:
                await memory.forget(str(found.get("id") or ""))
            except Exception:  # cleanup is best-effort; never mask the result
                logger.debug("self-check probe cleanup failed", exc_info=True)
        return (
            None
            if found is not None
            else (
                f"memory self-check FAILED: wrote a probe into collection {scope!r} but search "
                "did not return it. Memory writes and reads are out of sync — look for a mem0 "
                "API signature mismatch (e.g. search() filters) in lattice.memory.mem0_qdrant"
            )
        )

    detail = asyncio.run(_round_trip())
    if detail is not None:
        raise RuntimeError(detail)


# Explicit teardown at exit; without it QdrantClient.__del__ closes during
# interpreter finalisation and aborts the process. Registered once at import.
atexit.register(close_memory)
