"""Telegram media helpers."""

from __future__ import annotations

from pathlib import Path


async def save_inbound_file(file_path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / file_path.name
    target.write_bytes(file_path.read_bytes())
    return target
