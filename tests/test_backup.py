"""Backup / restore Lattice home archives."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lattice.backup import create_backup, read_manifest, restore_backup
from lattice.setup import init_home


def _seed_home(home: Path) -> None:
    init_home(home)
    (home / "workspace" / "note.txt").write_text("hello", encoding="utf-8")
    (home / "logs").mkdir(exist_ok=True)
    (home / "logs" / "lattice.log").write_text("log line\n", encoding="utf-8")
    (home / "gateway.pid").write_text("999999", encoding="utf-8")
    # Write via sqlite so we can verify consistent restore.
    db = home / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('seed')")
        conn.commit()


def test_backup_and_restore_roundtrip(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _seed_home(home)
    archive = tmp_path / "pack.tar.gz"

    result = create_backup(home, output=archive, include_logs=False)
    assert result.archive.is_file() and result.file_count >= 3
    manifest = read_manifest(archive)
    assert manifest["format"] == "lattice-home-v1"
    assert manifest["include_logs"] is False

    # Archive must not contain logs or pid.
    import tarfile

    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert "home/workspace/note.txt" in names
    assert "home/gateway.pid" not in names
    assert not any(n.startswith("home/logs/") for n in names)

    dest = tmp_path / "restored"
    restored = restore_backup(archive, dest)
    assert restored.file_count >= 3
    assert (dest / "workspace" / "note.txt").read_text(encoding="utf-8") == "hello"
    assert not (dest / "gateway.pid").exists()
    with sqlite3.connect(dest / "state.db") as conn:
        row = conn.execute("SELECT v FROM t").fetchone()
    assert row == ("seed",)


def test_restore_refuses_nonempty_without_force(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _seed_home(home)
    archive = tmp_path / "pack.tar.gz"
    create_backup(home, output=archive)

    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        restore_backup(archive, dest, force=False)

    result = restore_backup(archive, dest, force=True)
    assert result.displaced is not None
    assert result.displaced.is_dir()
    assert (result.displaced / "keep.txt").is_file()
    assert (dest / "workspace" / "note.txt").is_file()


def test_backup_include_logs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _seed_home(home)
    archive = tmp_path / "with-logs.tar.gz"
    create_backup(home, output=archive, include_logs=True)
    import tarfile

    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert "home/logs/lattice.log" in names
