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
