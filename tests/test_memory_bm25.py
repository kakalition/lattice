"""Memory BM25 normalization, lexical-merge, and reindex tests (offline)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from lattice.memory.mem0_qdrant import (
    _BM25_LEMMA_VERSION,
    Mem0QdrantMemory,
    _format_results,
    _merge_lexical,
)

# --- lattice.text normalization --------------------------------------------


def test_normalize_stems_and_splits() -> None:
    from lattice.text import normalize, tokenize

    assert tokenize("generateChart") == ["generate", "chart"]
    assert tokenize("Charts") == ["chart"]
    assert normalize("Charts and graphs") == "chart and graph"
    # ``ss`` words are not over-stripped; a single char is dropped.
    assert tokenize("class a") == ["class"]


# --- mem0 lemmatizer patch -------------------------------------------------


def test_silence_replaces_mem0_lemmatizer() -> None:
    from lattice.memory.mem0_qdrant import _silence_mem0_deps
    from lattice.text import normalize

    _silence_mem0_deps()
    import mem0.memory.main as mem0_main
    import mem0.utils.lemmatization as mem0_lemmatization

    assert mem0_main.lemmatize_for_bm25 is normalize
    assert mem0_lemmatization.lemmatize_for_bm25 is normalize
    assert mem0_main.lemmatize_for_bm25("Charts") == "chart"


# --- result shaping / merge ------------------------------------------------


def test_format_results_reads_mem0_shapes() -> None:
    out = _format_results(
        {"results": [{"id": "1", "memory": "flat white", "score": 0.8, "metadata": {"k": "v"}}]}
    )
    assert out == [{"id": "1", "text": "flat white", "metadata": {"k": "v"}, "score": 0.8}]
    # Bare list and id-less rows still shape up.
    assert _format_results([{"memory": "x"}])[0]["text"] == "x"
    assert _format_results(None) == []


def _row(name: str) -> dict[str, Any]:
    return {"id": name, "text": name, "metadata": {}}


def test_merge_reserves_slots_for_lexical_only() -> None:
    dense = [_row("d0"), _row("d1"), _row("d2"), _row("d3"), _row("d4")]
    lexical = [_row("l0"), _row("l1")]
    merged = _merge_lexical(dense, lexical, 5)
    assert [r["id"] for r in merged] == ["d0", "d1", "d2", "l0", "l1"]


def test_merge_excludes_lexical_duplicates() -> None:
    dense = [_row("d0"), _row("d1")]
    lexical = [_row("d0"), _row("l1")]
    merged = _merge_lexical(dense, lexical, 5)
    # Both dense rows kept, unused slots filled by the fresh lexical hit only.
    assert [r["id"] for r in merged] == ["d0", "d1", "l1"]


def test_merge_fills_from_lexical_when_dense_is_thin() -> None:
    dense = [_row("d0")]
    lexical = [_row("l0"), _row("l1"), _row("l2"), _row("l3")]
    merged = _merge_lexical(dense, lexical, 5)
    assert merged[0]["id"] == "d0"
    assert len(merged) == 5
    assert [r["id"] for r in merged[1:]] == ["l0", "l1", "l2", "l3"]


def test_merge_limit_one_keeps_top_dense() -> None:
    merged = _merge_lexical([_row("d0")], [_row("l0")], 1)
    assert [r["id"] for r in merged] == ["d0"]


def test_merge_limit_one_uses_lexical_when_dense_empty() -> None:
    merged = _merge_lexical([], [_row("l0")], 1)
    assert [r["id"] for r in merged] == ["l0"]


def test_merge_no_lexical_returns_dense() -> None:
    assert _merge_lexical([_row("d0")], [], 5) == [_row("d0")]


# --- lexical retrieval pass ------------------------------------------------


class _Point:
    def __init__(self, pid: str, score: float, payload: dict[str, Any]) -> None:
        self.id = pid
        self.score = score
        self.payload = payload


class _FakeVectorStore:
    def __init__(self, *, has_slot: bool = True, points: list[_Point] | None = None) -> None:
        self._has_bm25_slot = has_slot
        self.points = points or []
        self.calls: list[tuple[str, int, dict[str, Any] | None]] = []

    def keyword_search(
        self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None
    ) -> list[_Point]:
        self.calls.append((query, top_k, filters))
        return self.points


def _memory_with(vector_store: Any) -> Mem0QdrantMemory:
    mem = Mem0QdrantMemory.__new__(Mem0QdrantMemory)
    mem.collection = "lattice-test"
    mem.path = Path("/tmp/unused")  # type: ignore[assignment]
    mem._memory = SimpleNamespace(vector_store=vector_store)  # type: ignore[assignment]
    return mem


def test_lexical_hits_normalizes_query_and_maps_points() -> None:
    vs = _FakeVectorStore(points=[_Point("a", 3.0, {"data": "Revenue charts"})])
    hits = _memory_with(vs)._lexical_hits("Charts", 5)
    query, top_k, filters = vs.calls[0]
    assert query == "chart"
    assert top_k == 60
    assert filters == {"user_id": "lattice-test"}
    assert hits == [{"id": "a", "text": "Revenue charts", "metadata": {}}]


def test_lexical_hits_skips_without_bm25_slot() -> None:
    vs = _FakeVectorStore(has_slot=False, points=[_Point("a", 1.0, {"data": "x"})])
    assert _memory_with(vs)._lexical_hits("x", 5) == []
    assert vs.calls == []


# --- reindex ---------------------------------------------------------------


class _FakePoint:
    def __init__(self, pid: str, payload: dict[str, Any]) -> None:
        self.id = pid
        self.payload = payload


class _FakeClient:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points
        self.updated: list[Any] = []
        self.payload_updates: list[tuple[dict[str, Any], Any]] = []

    def scroll(self, collection_name: str, limit: int, offset: Any, **kwargs: Any):
        return (self.points if offset is None else [], None)

    def update_vectors(self, collection_name: str, points: list[Any]) -> None:
        self.updated.extend(points)

    def set_payload(self, collection_name: str, payload: dict[str, Any], points: list[Any]) -> None:
        self.payload_updates.append((payload, points))


class _ReindexVectorStore:
    _has_bm25_slot = True

    def __init__(self, points: list[_FakePoint]) -> None:
        self.collection_name = "lattice-test"
        self.client = _FakeClient(points)
        self.encoded: list[str] = []

    def _encode_bm25(self, text: str) -> Any:
        from qdrant_client import models

        self.encoded.append(text)
        return models.SparseVector(indices=[1], values=[1.0])


def _reindex_memory(tmp_path: Path, vs: Any) -> Mem0QdrantMemory:
    mem = Mem0QdrantMemory.__new__(Mem0QdrantMemory)
    mem.collection = "lattice-test"
    mem.path = tmp_path
    mem._memory = SimpleNamespace(vector_store=vs)  # type: ignore[assignment]
    return mem


def test_reindex_rewrites_stale_payload_and_sparse_vector(tmp_path: Path) -> None:
    vs = _ReindexVectorStore([_FakePoint("p1", {"data": "Charts and graphs"})])
    mem = _reindex_memory(tmp_path, vs)
    mem._ensure_bm25_reindex()

    assert len(vs.client.updated) == 1
    point_vectors = vs.client.updated[0]
    assert vs.encoded == ["chart and graph"], "BM25 re-encoded from normalized text"
    assert point_vectors.vector["bm25"].indices == [1]
    assert vs.client.payload_updates == [({"text_lemmatized": "chart and graph"}, ["p1"])]

    marker = tmp_path / ".lattice" / f"bm25-lemma-{mem.collection}.version"
    assert marker.read_text(encoding="utf-8") == str(_BM25_LEMMA_VERSION)


def test_reindex_is_a_noop_when_marker_matches(tmp_path: Path) -> None:
    vs = _ReindexVectorStore([_FakePoint("p1", {"data": "Charts"})])
    first = _reindex_memory(tmp_path, vs)
    first._ensure_bm25_reindex()
    assert len(vs.client.updated) == 1

    # A fresh instance (new process boot) must skip via the marker.
    second = _reindex_memory(tmp_path, vs)
    second._ensure_bm25_reindex()
    assert len(vs.client.updated) == 1, "already-migrated collection reindexed again"


def test_reindex_skips_rows_already_normalized(tmp_path: Path) -> None:
    vs = _ReindexVectorStore([_FakePoint("p1", {"data": "Charts", "text_lemmatized": "chart"})])
    mem = _reindex_memory(tmp_path, vs)
    mem._ensure_bm25_reindex()
    assert vs.client.updated == []
    marker = tmp_path / ".lattice" / f"bm25-lemma-{mem.collection}.version"
    assert marker.is_file()
