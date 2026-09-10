"""File read/write/edit/search tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

from lattice.tools.file_safety import resolve_agent_path, resolve_in_workspace


async def read_file(path: str, *, workspace: Path, home: Path | None = None) -> str:
    target = (
        resolve_agent_path(path, workspace, home=home)
        if home is not None
        else resolve_in_workspace(path, workspace)
    )
    return await asyncio.to_thread(target.read_text, encoding="utf-8")


async def write_file(
    path: str, content: str, *, workspace: Path, home: Path | None = None
) -> str:
    target = (
        resolve_agent_path(path, workspace, home=home)
        if home is not None
        else resolve_in_workspace(path, workspace)
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_text, content, encoding="utf-8")
    return f"wrote {target} ({len(content)} bytes)"


async def edit_file(
    path: str, old: str, new: str, *, workspace: Path, home: Path | None = None
) -> str:
    target = (
        resolve_agent_path(path, workspace, home=home)
        if home is not None
        else resolve_in_workspace(path, workspace)
    )
    text = await asyncio.to_thread(target.read_text, encoding="utf-8")
    if old not in text:
        raise ValueError("old_string not found")
    if text.count(old) > 1:
        raise ValueError("old_string not unique")
    updated = text.replace(old, new, 1)
    await asyncio.to_thread(target.write_text, updated, encoding="utf-8")
    return f"edited {target}"


async def search_files(pattern: str, *, workspace: Path, glob: str = "**/*") -> str:
    workspace = workspace.resolve()
    matches: list[str] = []

    def _search() -> list[str]:
        out: list[str] = []
        for path in workspace.glob(glob):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pattern in text:
                rel = path.relative_to(workspace)
                out.append(str(rel))
                if len(out) >= 50:
                    break
        return out

    matches = await asyncio.to_thread(_search)
    return "\n".join(matches) if matches else "(no matches)"
