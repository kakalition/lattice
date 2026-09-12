"""sqlite-admin script ↔ runtime registry interop (databases.yaml)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

from lattice.config import LatticeSettings
from lattice.sqlite.registry import SqliteRegistry

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "lattice"
    / "assets"
    / "skills"
    / "sqlite-admin"
    / "scripts"
    / "sqlite.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("lattice_sqlite_admin", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_reads_runtime_written_yaml(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / "sqlite").mkdir(parents=True)
    (home / "sqlite" / "databases.yaml").write_text(
        yaml.safe_dump(
            {"databases": {"finances": {"path": str(home / "finances.db"), "read_only": False}}},
            sort_keys=True,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LATTICE_HOME", str(home))
    script = _load_script()
    assert script.load_registry() == {
        "finances": {"path": str(home / "finances.db"), "read_only": False}
    }


def test_script_write_is_readable_by_runtime(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("LATTICE_HOME", str(home))
    script = _load_script()
    script.save_registry({"ledger": {"path": str(home / "ledger.db"), "read_only": True}})
    assert (home / "sqlite" / "databases.yaml").is_file()

    registry = SqliteRegistry(LatticeSettings(home=home))
    entries = {d.name: d for d in registry.list()}
    assert entries["ledger"].read_only is True


def test_script_reads_flat_and_nested_yaml(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / "sqlite").mkdir(parents=True)
    (home / "sqlite" / "databases.yaml").write_text(
        'databases:\n  finances:\n    path: "/tmp/my db: x.db"\n    read_only: true\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LATTICE_HOME", str(home))
    script = _load_script()
    assert script.load_registry() == {"finances": {"path": "/tmp/my db: x.db", "read_only": True}}


def test_script_fallback_parser_without_pyyaml(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / "sqlite").mkdir(parents=True)
    (home / "sqlite" / "databases.yaml").write_text(
        "databases:\n  notes:\n    path: '/tmp/a:b.db'\n    read_only: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LATTICE_HOME", str(home))
    monkeypatch.setitem(sys.modules, "yaml", None)
    script = _load_script()
    assert script.load_registry() == {"notes": {"path": "/tmp/a:b.db", "read_only": False}}
