"""Bundled skill assets refresh without clobbering operator edits."""

from __future__ import annotations

from pathlib import Path

from lattice.setup import refresh_bundled_skills


def _assets(tmp_path: Path, content: str) -> Path:
    assets = tmp_path / "assets"
    target = assets / "demo" / "scripts" / "run.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return assets


def test_missing_file_is_installed_and_manifest_written(tmp_path: Path) -> None:
    assets = _assets(tmp_path, "v1\n")
    home = tmp_path / "home"
    home.mkdir()

    updated = refresh_bundled_skills(home, assets_dir=assets, starters={})

    assert "demo/scripts/run.py" in updated
    installed = home / "skills" / "demo" / "scripts" / "run.py"
    assert installed.read_text() == "v1\n"
    assert (home / "skills" / ".bundled.json").is_file()


def test_unmodified_file_is_refreshed(tmp_path: Path) -> None:
    assets = _assets(tmp_path, "v1\n")
    home = tmp_path / "home"
    home.mkdir()
    refresh_bundled_skills(home, assets_dir=assets, starters={})

    (assets / "demo" / "scripts" / "run.py").write_text("v2\n")
    updated = refresh_bundled_skills(home, assets_dir=assets, starters={})

    assert "demo/scripts/run.py" in updated
    installed = home / "skills" / "demo" / "scripts" / "run.py"
    assert installed.read_text() == "v2\n"


def test_operator_edit_is_preserved(tmp_path: Path) -> None:
    assets = _assets(tmp_path, "v1\n")
    home = tmp_path / "home"
    home.mkdir()
    refresh_bundled_skills(home, assets_dir=assets, starters={})

    installed = home / "skills" / "demo" / "scripts" / "run.py"
    installed.write_text("my local changes\n")
    (assets / "demo" / "scripts" / "run.py").write_text("v2\n")

    updated = refresh_bundled_skills(home, assets_dir=assets, starters={})

    assert "demo/scripts/run.py" not in updated
    assert installed.read_text() == "my local changes\n"


def test_legacy_home_without_manifest_is_adopted_with_backup(tmp_path: Path) -> None:
    assets = _assets(tmp_path, "new\n")
    home = tmp_path / "home"
    installed = home / "skills" / "demo" / "scripts" / "run.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("stale\n")

    updated = refresh_bundled_skills(home, assets_dir=assets, starters={})

    assert "demo/scripts/run.py" in updated
    assert installed.read_text() == "new\n"
    backup = installed.with_suffix(installed.suffix + ".bundled.bak")
    assert backup.read_text() == "stale\n"
