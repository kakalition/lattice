"""Named multi-SQLite registry (separate from state.db)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lattice.config import LatticeSettings, SqliteDatabaseConfig
from lattice.paths import lattice_home
from lattice.tools.file_safety import is_denied_path


@dataclass
class DbEntry:
    name: str
    path: Path
    read_only: bool = False


class SqliteRegistry:
    def __init__(self, settings: LatticeSettings) -> None:
        self.settings = settings
        self._dbs: dict[str, DbEntry] = {}
        for name, cfg in settings.sqlite.databases.items():
            self._dbs[name] = DbEntry(
                name=name,
                path=Path(cfg.path).expanduser().resolve(),
                read_only=cfg.read_only,
            )

    def list(self, allow: list[str] | None = None) -> list[DbEntry]:
        items = list(self._dbs.values())
        if allow is None:
            return items
        allowed = set(allow)
        return [d for d in items if d.name in allowed]

    def get(self, name: str, allow: list[str] | None = None) -> DbEntry:
        if allow is not None and name not in allow:
            raise PermissionError(f"sqlite db not allowed for profile: {name}")
        if name not in self._dbs:
            raise KeyError(f"unknown sqlite db: {name}")
        return self._dbs[name]

    def register(self, name: str, path: str | Path, *, read_only: bool = False) -> DbEntry:
        if name == "state":
            raise ValueError("cannot register session state.db")
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = lattice_home() / "sqlite" / f"{name}.db"
        target = target.resolve()
        if is_denied_path(target) or target.name == "state.db":
            raise PermissionError("path denied for sqlite register")
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = DbEntry(name=name, path=target, read_only=read_only)
        self._dbs[name] = entry
        self.settings.sqlite.databases[name] = SqliteDatabaseConfig(
            path=str(target), read_only=read_only
        )
        return entry

    def unregister(self, name: str) -> None:
        self._dbs.pop(name, None)
        self.settings.sqlite.databases.pop(name, None)
