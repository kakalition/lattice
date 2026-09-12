"""Turn-scoped read cache: elide unchanged file bodies within a single turn.

Re-reading the same file after an edit burns tokens without new information.
The cache records ``(mtime_ns, size)`` per ``(scope, path)`` where the scope is
``session_id:turn_id``; a repeat read *within the same turn* returns a short
marker instead of the body. Any write changes mtime/size, so the next read is
fresh. The turn namespace matters because tool results are dropped from replayed
history: a body read on a previous turn is not in context, so it must be served
again rather than elided. Bounded and lock-guarded; best-effort.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

_MAX_ENTRIES = 128

_lock = threading.Lock()
_cache: OrderedDict[tuple[str, str], tuple[int, int]] = OrderedDict()


def is_unchanged(scope: str, path: str, mtime_ns: int, size: int) -> bool:
    key = (scope, path)
    with _lock:
        stored = _cache.get(key)
        if stored != (mtime_ns, size):
            return False
        _cache.move_to_end(key)
        return True


def record_read(scope: str, path: str, mtime_ns: int, size: int) -> None:
    key = (scope, path)
    with _lock:
        _cache[key] = (mtime_ns, size)
        _cache.move_to_end(key)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_session(session_id: str) -> None:
    """Drop all scopes for a session, including per-turn namespaced entries."""
    prefix = f"{session_id}:"
    with _lock:
        for key in [k for k in _cache if k[0] == session_id or k[0].startswith(prefix)]:
            _cache.pop(key, None)


def reset() -> None:
    with _lock:
        _cache.clear()
