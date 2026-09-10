"""Gateway pidfile lock."""

from __future__ import annotations

import atexit
import os
from pathlib import Path

from lattice.paths import lattice_home


class PidfileLock:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (lattice_home() / "gateway.pid")
        self._held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                old = int(self.path.read_text().strip())
                os.kill(old, 0)
                raise RuntimeError(f"gateway already running (pid {old})")
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        self._held = True
        atexit.register(self.release)

    def release(self) -> None:
        if self._held and self.path.exists():
            try:
                if self.path.read_text().strip() == str(os.getpid()):
                    self.path.unlink(missing_ok=True)
            except OSError:
                pass
        self._held = False
