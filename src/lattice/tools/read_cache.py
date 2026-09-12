"""Session-scoped read cache: elide unchanged file bodies.

Re-reading the same file after an edit burns tokens without new information.
The cache records ``(mtime_ns, size)`` per ``(session, path)``; a repeat read
returns a short marker instead of the body. Any write changes mtime/size, so the
next read is fresh. Bounded and lock-guarded; best-effort.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

_MAX_ENTRIES = 128

_lock = threading.Lock()
_cache: OrderedDict[tuple[str, str], tuple[int, int]] = OrderedDict()


def is_unchanged(session_id: str, path: str, mtime_ns: int, size: int) -> bool:
    key = (session_id, path)
    with _lock:
        stored = _cache.get(key)
        if stored != (mtime_ns, size):
            return False
        _cache.move_to_end(key)
        return True


def record_read(session_id: str, path: str, mtime_ns: int, size: int) -> None:
    key = (session_id, path)
    with _lock:
        _cache[key] = (mtime_ns, size)
        _cache.move_to_end(key)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_session(session_id: str) -> None:
    with _lock:
        for key in [k for k in _cache if k[0] == session_id]:
            _cache.pop(key, None)


def reset() -> None:
    with _lock:
        _cache.clear()
