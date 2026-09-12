"""Serialized background memory writer.

``run_turn`` used to ``await memory.sync_turn(...)`` before returning the reply,
so the channel send waited on a fastembed + Qdrant write (measured 8–73s on the
VPS). Persistence does not need to gate the reply, so turns are queued here and
drained by a single worker task per process.

Why one worker: the embedded Qdrant client (``_shared_qdrant_client``) allows a
single opener per path, and mem0 instances are process-cached. A second writer
would race the cache; serializing through one queue keeps writes ordered.

Durability trade-off: an abrupt crash between reply and flush can lose the last
turn's memory. ``flush_memory`` is awaited on gateway/CLI shutdown, and
``close_memory`` drains best-effort at interpreter exit.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from lattice.tools.deadline import with_deadline

if TYPE_CHECKING:
    from lattice.memory.base import Memory

logger = logging.getLogger("lattice.memory.worker")

# Total flush budget on shutdown; small enough not to hang a service stop.
DEFAULT_FLUSH_TIMEOUT_S = 5.0
# Per-job bound so one hung write cannot starve the queue.
DEFAULT_SYNC_TIMEOUT_S = 60.0


@dataclass
class _Job:
    memory: Memory
    messages: list[dict[str, Any]]
    turn_id: str | None = None
    timeout: float = DEFAULT_SYNC_TIMEOUT_S
    enqueued: float = field(default_factory=time.monotonic)


_lock = threading.Lock()
_jobs: deque[_Job] = deque()
_wake: asyncio.Event | None = None
_worker: asyncio.Task[None] | None = None
_loop: asyncio.AbstractEventLoop | None = None
_inflight = 0


def _pending_unlocked() -> int:
    return _inflight


def _next_job() -> _Job | None:
    with _lock:
        if _jobs:
            return _jobs.popleft()
    return None


def _ensure_worker() -> None:
    """(Re)create the worker task for the current running loop."""
    global _wake, _worker, _loop
    loop = asyncio.get_running_loop()
    new_worker = _worker is None or _worker.done() or _loop is not loop
    if not new_worker:
        event = _wake
        if event is not None:
            event.set()
        return
    # Carry any jobs queued for a previous (now-dead) loop into the new one.
    with _lock:
        _loop = loop
        _wake = asyncio.Event()
        _worker = loop.create_task(_drain())


async def _drain() -> None:
    while True:
        if not _jobs:
            event = _wake
            if event is None:
                return
            event.clear()
            if _jobs:
                continue
            try:
                await event.wait()
            except asyncio.CancelledError:
                return
            continue
        job = _next_job()
        if job is not None:
            await _run(job)


async def _run(job: _Job) -> None:
    global _inflight
    started = time.monotonic()
    try:
        await with_deadline(
            job.memory.sync_turn(job.messages),
            seconds=job.timeout,
            label="memory_sync",
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        logger.warning("memory sync timed out after %.1fs turn=%s", job.timeout, job.turn_id)
    except Exception:
        logger.warning("background memory sync failed", exc_info=True)
    finally:
        with _lock:
            _inflight = max(0, _inflight - 1)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if job.turn_id:
            logger.info("memory_sync_ms=%d background=true turn=%s", elapsed_ms, job.turn_id)
        else:
            logger.info("memory_sync_ms=%d background=true", elapsed_ms)


def enqueue_sync(
    memory: Memory,
    messages: list[dict[str, Any]],
    *,
    turn_id: str | None = None,
    timeout: float = DEFAULT_SYNC_TIMEOUT_S,
) -> None:
    """Queue a turn for background persistence. Never blocks the caller."""
    if not messages:
        return
    global _inflight
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No event loop: the caller is synchronous. Persist inline in a fresh loop
        # rather than silently dropping the turn.
        with _lock:
            _inflight += 1
        _sync_run(_Job(memory=memory, messages=list(messages), turn_id=turn_id, timeout=timeout))
        return
    with _lock:
        _jobs.append(_Job(memory=memory, messages=list(messages), turn_id=turn_id, timeout=timeout))
        _inflight += 1
    _ensure_worker()


def pending_jobs() -> int:
    """Jobs queued or currently running (observability / tests)."""
    with _lock:
        return _pending_unlocked()


async def flush_memory(timeout: float = DEFAULT_FLUSH_TIMEOUT_S) -> None:
    """Wait, bounded, for all queued memory jobs to finish."""
    loop = asyncio.get_running_loop()
    with _lock:
        if _loop is not loop or _worker is None:
            return
        event = _wake
    if event is not None:
        event.set()
    deadline = loop.time() + timeout
    while pending_jobs() > 0:
        if loop.time() >= deadline:
            logger.warning("memory flush timed out with %d job(s) pending", pending_jobs())
            return
        await asyncio.sleep(0.05)


def drain_memory_sync(timeout: float = DEFAULT_FLUSH_TIMEOUT_S) -> None:
    """Best-effort synchronous drain for shutdown outside a running loop."""
    global _inflight
    if _loop is not None and _loop.is_running():
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = _next_job()
        if job is None:
            if pending_jobs() == 0:
                return
            time.sleep(0.01)
            continue
        _sync_run(job)


def _sync_run(job: _Job) -> None:
    global _inflight
    started = time.monotonic()
    try:
        asyncio.run(asyncio.wait_for(job.memory.sync_turn(job.messages), timeout=job.timeout))
    except TimeoutError:
        logger.warning("memory sync timed out during shutdown drain after %.1fs", job.timeout)
    except Exception:
        logger.warning("memory sync failed during shutdown drain", exc_info=True)
    finally:
        with _lock:
            _inflight = max(0, _inflight - 1)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info("memory_sync_ms=%d background=false", elapsed_ms)
