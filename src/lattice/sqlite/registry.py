"""Named multi-SQLite registry (separate from state.db).

Operator baseline: ``lattice.yaml`` → ``sqlite.databases``.
Agent-authored DBs persist under ``<home>/sqlite/databases.yaml`` so
``sqlite_register`` / ``sqlite_unregister`` survive gateway restarts without
editing ``lattice.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from lattice.config import LatticeSettings, SqliteDatabaseConfig
from lattice.tools.file_safety import is_denied_path


class DbEntry(BaseModel):
    name: str
    path: Path
    read_only: bool = False
    # True when register() created a new, empty database file at ``path``.
    created: bool = False


def databases_store_path(home: Path) -> Path:
    return home.expanduser().resolve() / "sqlite" / "databases.yaml"


def _load_persisted(home: Path) -> dict[str, SqliteDatabaseConfig]:
    path = databases_store_path(home)
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    # Accept either {databases: {name: …}} or flat {name: …}
    data = raw.get("databases", raw)
    if not isinstance(data, dict):
        return {}
    out: dict[str, SqliteDatabaseConfig] = {}
    for name, cfg in data.items():
        if not isinstance(name, str) or name == "state":
            continue
        if isinstance(cfg, str):
            out[name] = SqliteDatabaseConfig(path=cfg)
        elif isinstance(cfg, dict) and cfg.get("path"):
            out[name] = SqliteDatabaseConfig(
                path=str(cfg["path"]),
                read_only=bool(cfg.get("read_only", False)),
            )
    return out


def _write_persisted(home: Path, databases: dict[str, SqliteDatabaseConfig]) -> None:
    path = databases_store_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        name: {"path": cfg.path, "read_only": cfg.read_only}
        for name, cfg in sorted(databases.items())
    }
    path.write_text(
        yaml.safe_dump({"databases": payload}, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )


def _resolve_db_path(
    path: str | Path, *, home: Path, name: str, workspace: Path | None = None
) -> Path:
    target = Path(path).expanduser()
    if target.is_absolute():
        return target.resolve()
    # Prefer an existing workspace file: ``sqlite_register finance finance.db``
    # should find ``<workspace>/finance.db`` rather than silently creating an
    # empty ``<home>/sqlite/finance.db``.
    if workspace is not None:
        candidate = (workspace.expanduser() / target).resolve()
        if candidate.is_file():
            return candidate
    # Otherwise relative paths land under home/sqlite/<name>.db
    return (home / "sqlite" / f"{name}.db").resolve()


class SqliteRegistry:
    def __init__(self, settings: LatticeSettings, *, workspace: Path | None = None) -> None:
        self.settings = settings
        self._home = settings.home.expanduser().resolve()
        self._workspace = workspace.expanduser().resolve() if workspace else None
        self._dbs: dict[str, DbEntry] = {}
        self._persisted_names: set[str] = set()

        # Operator config first, then agent-persisted overlay
        merged: dict[str, SqliteDatabaseConfig] = dict(settings.sqlite.databases)
        persisted = _load_persisted(self._home)
        self._persisted_names = set(persisted)
        merged.update(persisted)

        for name, cfg in merged.items():
            resolved = _resolve_db_path(
                cfg.path, home=self._home, name=name, workspace=self._workspace
            )
            self._dbs[name] = DbEntry(name=name, path=resolved, read_only=cfg.read_only)
            # Keep settings in sync for callers that read settings.sqlite.databases
            self.settings.sqlite.databases[name] = SqliteDatabaseConfig(
                path=str(resolved), read_only=cfg.read_only
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
        target = _resolve_db_path(path, home=self._home, name=name, workspace=self._workspace)
        if is_denied_path(target) or target.name == "state.db":
            raise PermissionError("path denied for sqlite register")
        target.parent.mkdir(parents=True, exist_ok=True)
        created = not target.exists()
        if created:
            target.touch()
        entry = DbEntry(name=name, path=target, read_only=read_only, created=created)
        self._dbs[name] = entry
        cfg = SqliteDatabaseConfig(path=str(target), read_only=read_only)
        self.settings.sqlite.databases[name] = cfg
        self._persisted_names.add(name)
        self._flush_persisted()
        return entry

    def unregister(self, name: str) -> None:
        self._dbs.pop(name, None)
        self.settings.sqlite.databases.pop(name, None)
        self._persisted_names.discard(name)
        self._flush_persisted()

    def _flush_persisted(self) -> None:
        payload: dict[str, SqliteDatabaseConfig] = {}
        for name in sorted(self._persisted_names):
            entry = self._dbs.get(name)
            if entry is None:
                continue
            payload[name] = SqliteDatabaseConfig(path=str(entry.path), read_only=entry.read_only)
        _write_persisted(self._home, payload)
