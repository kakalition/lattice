"""Compile / restore Lattice home (``.lattice``) archives."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from lattice import __version__
from lattice.paths import lattice_home

FORMAT = "lattice-home-v1"
MANIFEST_NAME = "MANIFEST.json"

# Always skip these relative paths (posix) when packing.
_ALWAYS_SKIP_NAMES = frozenset(
    {
        "gateway.pid",
        ".DS_Store",
        "Thumbs.db",
        "__pycache__",
    }
)
_ALWAYS_SKIP_SUFFIXES = (".pyc", ".pyo", ".tmp")


class BackupResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    archive: Path
    home: Path
    bytes: int
    file_count: int


class RestoreResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    home: Path
    archive: Path
    displaced: Path | None
    file_count: int


def default_backup_path(home: Path | None = None, *, dest_dir: Path | None = None) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    label = (home or lattice_home()).name
    name = f"{label}-{stamp}.tar.gz"
    parent = dest_dir or Path.cwd()
    return parent / name


def _rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _should_skip(rel: str, *, include_logs: bool) -> bool:
    parts = rel.split("/")
    if any(p in _ALWAYS_SKIP_NAMES for p in parts):
        return True
    if any(rel.endswith(suf) for suf in _ALWAYS_SKIP_SUFFIXES):
        return True
    if not include_logs and (parts[0] == "logs" or rel == "logs"):
        return True
    # WAL sidecars for state.db — we ship a consistent online copy instead.
    return rel in {"state.db-wal", "state.db-shm"}


def _iter_home_files(home: Path, *, include_logs: bool) -> Iterable[Path]:
    for path in sorted(home.rglob("*")):
        if not path.is_file():
            continue
        rel = _rel_posix(path, home)
        if _should_skip(rel, include_logs=include_logs):
            continue
        yield path


def _consistent_sqlite_copy(src: Path, dest: Path) -> None:
    """Online backup so WAL state is consistent inside the archive.

    Falls back to a plain copy when ``src`` is not a usable SQLite database
    (corrupt or placeholder), so backup/reset can still complete.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with (
            sqlite3.connect(f"file:{src}?mode=ro", uri=True) as src_conn,
            sqlite3.connect(dest) as dst_conn,
        ):
            src_conn.backup(dst_conn)
    except sqlite3.Error:
        shutil.copy2(src, dest)


def gateway_running(home: Path) -> int | None:
    pid_path = home / "gateway.pid"
    if not pid_path.is_file():
        return None
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return None
    return pid


def create_backup(
    home: Path | None = None,
    *,
    output: Path | None = None,
    include_logs: bool = False,
) -> BackupResult:
    """Compile ``home`` into a ``.tar.gz`` archive."""
    root = (home or lattice_home()).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Lattice home not found: {root}")

    out = (output or default_backup_path(root)).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise FileExistsError(f"archive already exists: {out}")

    files = list(_iter_home_files(root, include_logs=include_logs))
    excludes = ["gateway.pid", "state.db-wal", "state.db-shm", *_ALWAYS_SKIP_NAMES]
    if not include_logs:
        excludes.append("logs/")

    manifest: dict[str, Any] = {
        "format": FORMAT,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "lattice_version": __version__,
        "source_home": str(root),
        "include_logs": include_logs,
        "excludes": sorted(set(excludes)),
        "file_count": 0,
    }

    with tempfile.TemporaryDirectory(prefix="lattice-backup-") as tmp:
        staging = Path(tmp)
        data_root = staging / "home"
        data_root.mkdir()

        copied = 0
        for path in files:
            rel = _rel_posix(path, root)
            dest = data_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if rel == "state.db":
                _consistent_sqlite_copy(path, dest)
            else:
                shutil.copy2(path, dest)
            copied += 1

        # If state.db was skipped somehow but exists, still try consistent copy.
        state = root / "state.db"
        if state.is_file() and not (data_root / "state.db").exists():
            _consistent_sqlite_copy(state, data_root / "state.db")
            copied += 1

        manifest["file_count"] = copied
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with tarfile.open(out, "w:gz") as tar:
            tar.add(staging / MANIFEST_NAME, arcname=MANIFEST_NAME)
            tar.add(data_root, arcname="home")

    return BackupResult(
        archive=out,
        home=root,
        bytes=out.stat().st_size,
        file_count=copied,
    )


def read_manifest(archive: Path) -> dict[str, Any]:
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.getmember(MANIFEST_NAME)
        except KeyError as exc:
            raise ValueError(f"not a Lattice home archive (missing {MANIFEST_NAME})") from exc
        fh = tar.extractfile(member)
        if fh is None:
            raise ValueError("corrupt archive: empty manifest")
        data = json.loads(fh.read().decode("utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError(f"unsupported archive format: {data.get('format')!r}")
    return data


def _home_nonempty(home: Path) -> bool:
    if not home.exists():
        return False
    try:
        next(home.iterdir())
    except StopIteration:
        return False
    return True


def restore_backup(
    archive: Path,
    home: Path | None = None,
    *,
    force: bool = False,
) -> RestoreResult:
    """Restore a compiled archive into ``home``."""
    archive = archive.resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"archive not found: {archive}")

    read_manifest(archive)
    root = (home or lattice_home()).resolve()

    running = gateway_running(root)
    if running is not None and not force:
        raise RuntimeError(f"gateway appears running (pid {running}); stop it or pass --force")

    displaced: Path | None = None
    if _home_nonempty(root):
        if not force:
            raise FileExistsError(
                f"Lattice home is not empty: {root} (pass --force to displace it)"
            )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        displaced = root.with_name(f"{root.name}.bak.{stamp}")
        if displaced.exists():
            raise FileExistsError(f"backup displacement path exists: {displaced}")
        root.rename(displaced)

    root.mkdir(parents=True, exist_ok=True)

    file_count = 0
    with tempfile.TemporaryDirectory(prefix="lattice-restore-") as tmp:
        staging = Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            # Python 3.12+ supports filter=; use data filter when available.
            kwargs: dict[str, Any] = {}
            if hasattr(tarfile, "data_filter"):
                kwargs["filter"] = "data"
            tar.extractall(staging, **kwargs)

        home_src = staging / "home"
        if not home_src.is_dir():
            # Recover prior home if we displaced it.
            if displaced is not None and not _home_nonempty(root):
                try:
                    root.rmdir()
                except OSError:
                    shutil.rmtree(root, ignore_errors=True)
                displaced.rename(root)
            raise ValueError("corrupt archive: missing home/ directory")

        for path in sorted(home_src.rglob("*")):
            if not path.is_file():
                continue
            rel = _rel_posix(path, home_src)
            parts = rel.split("/")
            if any(p in _ALWAYS_SKIP_NAMES for p in parts) or any(
                rel.endswith(suf) for suf in _ALWAYS_SKIP_SUFFIXES
            ):
                continue
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            file_count += 1

    return RestoreResult(
        home=root,
        archive=archive,
        displaced=displaced,
        file_count=file_count,
    )
