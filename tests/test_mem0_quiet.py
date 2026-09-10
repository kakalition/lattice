"""Mem0/Qdrant quiet-init behavior."""

from __future__ import annotations

import logging
from pathlib import Path

from mem0.vector_stores.base import VectorStoreBase

from lattice.memory.mem0_qdrant import (
    Mem0QdrantMemory,
    _shared_qdrant_client,
    _silence_mem0_deps,
    build_memory,
)


def test_silence_mem0_deps_sets_env_and_log_levels(monkeypatch) -> None:
    monkeypatch.delenv("MEM0_TELEMETRY", raising=False)
    _silence_mem0_deps()
    assert logging.getLogger("mem0").level == logging.ERROR
    assert logging.getLogger("mem0.utils.spacy_models").level == logging.ERROR
    import os

    assert os.environ.get("MEM0_TELEMETRY") == "False"


def test_silence_marks_spacy_unavailable() -> None:
    _silence_mem0_deps()
    import mem0.utils.spacy_models as spacy_models

    assert spacy_models._load_failed_lemma is True
    assert spacy_models._load_failed_full is True
    assert spacy_models.get_nlp_lemma() is None
    assert spacy_models.get_nlp_full() is None


def test_shared_qdrant_client_reuses_instance(tmp_path: Path) -> None:
    path = tmp_path / "qdrant-shared"
    assert _shared_qdrant_client(path) is _shared_qdrant_client(path)


def test_build_memory_reuses_instance(tmp_path: Path) -> None:
    path = tmp_path / "qdrant-cache"
    a = build_memory(collection="c1", path=path)
    b = build_memory(collection="c1", path=path)
    assert a is b


def test_mem0_qdrant_init_enables_bm25(tmp_path: Path, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        mem = Mem0QdrantMemory(collection="quiet-test", path=tmp_path / "qdrant-init")
    joined = "\n".join(r.message for r in caplog.records)
    assert "spaCy" not in joined
    assert "PostHog" not in joined
    assert mem._memory is not None
    vs = mem._memory.vector_store
    assert type(vs).keyword_search is not VectorStoreBase.keyword_search
