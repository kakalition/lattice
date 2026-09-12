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


def test_mem0_search_uses_filters_not_top_level_user_id(monkeypatch, tmp_path: Path) -> None:
    """Regression: search() passed user_id= at the top level and always failed.

    mem0 v2 rejects top-level entity kwargs on search(), raising ValueError. A
    blanket ``except Exception`` then returned the empty fallback, so memory
    search silently returned nothing regardless of what had been stored.
    """
    import asyncio

    from lattice.memory.mem0_qdrant import Mem0QdrantMemory

    captured: dict = {}

    class _FakeInner:
        def search(self, query, **kwargs):
            captured["query"] = query
            captured["kwargs"] = kwargs
            if "user_id" in kwargs:
                raise ValueError(
                    "Top-level entity parameters frozenset({'user_id'}) are not supported "
                    "in search(). Use filters={'user_id': '...'} instead"
                )
            return {"results": [{"id": "1", "memory": "flat white", "metadata": {}}]}

    mem = Mem0QdrantMemory.__new__(Mem0QdrantMemory)
    mem.collection = "lattice-test"
    mem._fallback = None  # type: ignore[assignment]
    mem._memory = _FakeInner()  # type: ignore[assignment]

    hits = asyncio.run(mem.search("coffee", limit=5))

    assert captured["query"] == "coffee"
    assert "user_id" not in captured["kwargs"], "top-level user_id still passed"
    assert captured["kwargs"]["filters"] == {"user_id": "lattice-test"}
    assert hits and hits[0]["text"] == "flat white"


def test_mem0_llm_model_comes_from_config_not_the_builtin_default(tmp_path: Path) -> None:
    """Regression: mem0 silently used gpt-4o-mini regardless of config.

    mem0's own default LLM is gpt-4o-mini. It was only overridden by the legacy
    ``LATTICE_AGENT__MODEL`` env var, which nothing sets, so memory extraction
    billed OpenAI even when lattice.yaml pointed at another provider.
    """
    from lattice.memory.mem0_qdrant import _mem0_config

    cfg = _mem0_config(
        collection="c", path=tmp_path, client=None, llm_model="inception/mercury-2.5"
    )
    assert cfg["llm"]["config"]["model"] == "inception/mercury-2.5"
    assert cfg["llm"]["config"]["model"] != "gpt-4o-mini"


def test_mem0_llm_model_ignores_legacy_env(monkeypatch, tmp_path: Path) -> None:
    """The legacy env var must not outrank an explicit model."""
    from lattice.memory.mem0_qdrant import _mem0_config

    monkeypatch.setenv("LATTICE_AGENT__MODEL", "legacy/should-not-win")
    cfg = _mem0_config(
        collection="c", path=tmp_path, client=None, llm_model="inception/mercury-2.5"
    )
    assert cfg["llm"]["config"]["model"] == "inception/mercury-2.5"


def test_build_memory_for_profile_uses_auxiliary_model(tmp_path: Path, monkeypatch) -> None:
    """The mem0 backend must carry the profile's configured auxiliary model."""
    import lattice.agent_app as agent_app
    from lattice.config import LatticeSettings
    from lattice.profiles import Profile

    # Avoid touching the network/embedder: only assert the resolved model.
    captured: dict = {}

    def fake_build_memory(*, collection, path=None, llm_model=None, is_reasoning_model=None):
        captured["model"] = llm_model
        captured["reasoning"] = is_reasoning_model
        return object()

    monkeypatch.setattr(agent_app, "build_memory", fake_build_memory)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.auxiliary_model = "inception/mercury-2.5"

    agent_app.build_memory_for_profile(settings, Profile(id="p"))
    assert captured["model"] == "inception/mercury-2.5"

    agent_app.build_memory_for_profile(settings, Profile(id="q", auxiliary_model="other/model"))
    assert captured["model"] == "other/model"


def test_mem0_qdrant_init_enables_bm25(tmp_path: Path, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        mem = Mem0QdrantMemory(collection="quiet-test", path=tmp_path / "qdrant-init")
    joined = "\n".join(r.message for r in caplog.records)
    assert "spaCy" not in joined
    assert "PostHog" not in joined
    assert mem._memory is not None
    vs = mem._memory.vector_store
    assert type(vs).keyword_search is not VectorStoreBase.keyword_search


# --- shutdown --------------------------------------------------------------


def test_close_memory_clears_caches_and_is_idempotent(tmp_path: Path) -> None:
    import lattice.memory.mem0_qdrant as mq

    path = tmp_path / "qdrant-close"
    _shared_qdrant_client(path)
    build_memory(collection="close-test", path=path)
    assert mq._clients and mq._instances

    mq.close_memory()
    assert mq._clients == {}
    assert mq._instances == {}
    mq.close_memory()  # must not raise


def test_process_exits_cleanly_with_a_live_qdrant_client(tmp_path: Path) -> None:
    """Regression: closing Qdrant during interpreter finalisation aborts the process.

    ``QdrantClient.__del__`` -> ``close()`` lazily imports ``portalocker`` and
    closes collection storage. If that runs after interpreter shutdown starts it
    raises ``ImportError: sys.meta_path is None`` and libc++ then kills the
    process with ``recursive_mutex lock failed``. The ``atexit`` hook in
    ``mem0_qdrant`` closes clients while the interpreter is still usable, so a
    child process that merely creates a client must exit silently.
    """
    import subprocess
    import sys

    script = (
        "from pathlib import Path\n"
        "from lattice.memory.mem0_qdrant import _shared_qdrant_client\n"
        f"_shared_qdrant_client(Path({str(tmp_path / 'qdrant-exit')!r}))\n"
        "print('created', flush=True)\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "created" in proc.stdout
    assert "recursive_mutex" not in proc.stderr, "libc++ aborted at shutdown"
    assert "QdrantClient.__del__" not in proc.stderr, "client closed during finalisation"
    assert "ImportError" not in proc.stderr


def test_shutdown_crash_control_has_teeth(tmp_path: Path) -> None:
    """Guard the regression test: removing the hook must bring the crash back.

    If this ever stops reproducing (e.g. qdrant starts closing lazily and
    safely), the test above is no longer proving anything and both should be
    revisited rather than silently passing.
    """
    import subprocess
    import sys

    script = (
        "import atexit\n"
        "from pathlib import Path\n"
        "from lattice.memory.mem0_qdrant import _shared_qdrant_client, close_memory\n"
        "atexit.unregister(close_memory)\n"
        f"_shared_qdrant_client(Path({str(tmp_path / 'qdrant-control')!r}))\n"
        "print('created', flush=True)\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert "QdrantClient.__del__" in proc.stderr, (
        "pre-fix shutdown crash no longer reproduces; the regression test above "
        "may be vacuous — re-verify before removing"
    )


def test_client_created_after_close_is_still_reaped(tmp_path: Path) -> None:
    """A shutdown pass must not disarm later teardown.

    ``close_memory()`` can legitimately run mid-process (tests, restart). Any
    client created afterwards still needs closing at exit, so creating a client
    re-arms the hook.
    """
    import subprocess
    import sys

    script = (
        "from pathlib import Path\n"
        "from lattice.memory.mem0_qdrant import _shared_qdrant_client, close_memory\n"
        f"_shared_qdrant_client(Path({str(tmp_path / 'first')!r}))\n"
        "close_memory()\n"
        f"_shared_qdrant_client(Path({str(tmp_path / 'second')!r}))\n"
        "print('created', flush=True)\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "QdrantClient.__del__" not in proc.stderr, "client leaked past close_memory()"


# --- memory boot self-check -------------------------------------------------


def _fake_backend(tmp_path: Path, *, search_ok: bool = True) -> Mem0QdrantMemory:
    """A Mem0QdrantMemory whose mem0 inner object is a dict-backed stand-in."""
    from lattice.memory.mem0_qdrant import InMemoryMemory

    mem = Mem0QdrantMemory.__new__(Mem0QdrantMemory)
    mem.collection = "lattice-probecheck"
    mem.path = tmp_path
    # Real instances always carry a fallback; the wrapper's except paths use it.
    mem._fallback = InMemoryMemory("lattice-probecheck")  # type: ignore[assignment]

    class _Inner:
        def __init__(self) -> None:
            self.rows: dict[str, str] = {}

        def add(self, text, *, user_id=None, metadata=None, infer=True):
            if isinstance(text, list):
                text = text[0]["content"]
            if not infer:
                self.rows[text] = text
                return [{"id": text, "memory": text, "event": "ADD"}]
            self.rows[text] = text
            return {"results": [{"id": text, "memory": text, "event": "ADD"}]}

        def search(self, query, *, filters=None, limit=5):
            if not search_ok:
                raise ValueError("search API mismatch")
            assert filters and "user_id" in filters, "probe/search must scope by filters"
            hits = [{"id": k, "memory": v, "metadata": {}} for k, v in self.rows.items()]
            return {"results": hits[:limit]}

        def delete(self, memory_id):
            self.rows.pop(memory_id, None)

    mem._memory = _Inner()  # type: ignore[assignment]
    return mem


def test_probe_memory_round_trips_and_cleans_up(tmp_path: Path) -> None:
    """A healthy backend must pass and leave nothing behind."""
    from lattice.memory.mem0_qdrant import probe_memory

    mem = _fake_backend(tmp_path)
    probe_memory(mem, collection="lattice-probecheck")

    assert mem._memory.rows == {}, "probe row leaked into the store"  # type: ignore[union-attr]


def test_probe_memory_raises_when_search_is_broken(tmp_path: Path) -> None:
    """Regression: this is the exact shape of the silent search failure.

    Search raised, the wrapper swallowed it, and memory returned nothing forever.
    The probe must convert that into a loud error at boot.
    """
    import pytest

    from lattice.memory.mem0_qdrant import probe_memory

    mem = _fake_backend(tmp_path, search_ok=False)
    with pytest.raises(RuntimeError, match="search did not return it"):
        probe_memory(mem, collection="lattice-probecheck")


def test_probe_memory_raises_when_backend_is_unavailable(tmp_path: Path) -> None:
    """A working fallback is still a failure: memories would not persist."""
    import pytest

    from lattice.memory.mem0_qdrant import probe_memory

    mem = _fake_backend(tmp_path)
    mem._memory = None  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="backend is unavailable"):
        probe_memory(mem, collection="lattice-probecheck")


def test_probe_rows_never_reach_normal_search(tmp_path: Path) -> None:
    """Probe tokens are internal: they must not surface to the model."""
    import asyncio

    from lattice.memory.mem0_qdrant import _PROBE_PREFIX

    mem = _fake_backend(tmp_path)
    mem._memory.add(f"{_PROBE_PREFIX}-leftover", user_id=mem.collection, infer=False)  # type: ignore[union-attr]

    assert asyncio.run(mem.search("probe", limit=5)) == []
    # ...but the self-check can still see them, or it would always fail.
    assert asyncio.run(mem.search("probe", limit=5, include_probes=True))


def test_add_returns_the_real_memory_id(tmp_path: Path) -> None:
    """Regression: add() read result['id'] from a ``{"results": [...]}`` payload.

    That always missed, so add() handed back a fabricated uuid and any follow-up
    update/forget targeted a nonexistent memory.
    """
    import asyncio

    from lattice.memory.mem0_qdrant import _first_memory_id

    mem = _fake_backend(tmp_path)
    mid = asyncio.run(mem.add("user likes dark mode"))
    assert mid in mem._memory.rows  # type: ignore[union-attr]

    # Both shapes mem0 returns, plus the flat-dict fallback.
    assert _first_memory_id({"results": [{"id": "a"}]}) == "a"
    assert _first_memory_id([{"id": "b"}]) == "b"
    assert _first_memory_id({"id": "c"}) == "c"
    assert _first_memory_id({"results": []}) is None
    assert _first_memory_id(None) is None
