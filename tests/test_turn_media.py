"""Auto-attach of media produced during a turn."""

from __future__ import annotations

import os
import time
from pathlib import Path

from lattice.turn import discover_turn_media, snapshot_media


def _touch(path: Path, mtime: float, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (mtime, mtime))
    return path


def test_discovers_script_produced_media_after_turn_start(tmp_path: Path) -> None:
    turn_start = time.time()
    old = _touch(tmp_path / "old.png", turn_start - 600)
    before = snapshot_media(tmp_path)
    fresh = _touch(tmp_path / "chart.png", turn_start + 0.5)

    found = discover_turn_media(tmp_path, turn_start=turn_start, before=before, existing=set())

    assert found == [fresh.resolve()]
    assert old.resolve() not in found


def test_inbound_uploads_are_not_re_attached(tmp_path: Path) -> None:
    turn_start = time.time()
    before = snapshot_media(tmp_path)
    _touch(tmp_path / "inbound" / "photo-1.jpg", turn_start + 0.5)

    found = discover_turn_media(tmp_path, turn_start=turn_start, before=before, existing=set())

    assert found == []


def test_dedupes_against_existing_outbound_media(tmp_path: Path) -> None:
    turn_start = time.time()
    before = snapshot_media(tmp_path)
    chart = _touch(tmp_path / "chart.png", turn_start + 0.5)

    found = discover_turn_media(
        tmp_path, turn_start=turn_start, before=before, existing={chart.resolve()}
    )

    assert found == []


def test_caps_count_and_ignores_non_media(tmp_path: Path) -> None:
    turn_start = time.time()
    before = snapshot_media(tmp_path)
    for i in range(10):
        _touch(tmp_path / f"chart-{i}.png", turn_start + 0.5 + i)
    _touch(tmp_path / "notes.txt", turn_start + 0.5)

    found = discover_turn_media(tmp_path, turn_start=turn_start, before=before, existing=set())

    assert len(found) == 6
    # Oldest first, so the earliest produced chart survives the cap.
    assert found[0].name == "chart-0.png"
