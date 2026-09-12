"""File read/write/edit/search tools."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from lattice.tools.file_safety import resolve_agent_path, resolve_in_workspace

# Reads larger than this are headed with a line count and a continue hint so a
# single big script cannot flood the model's context.
READ_MAX_BYTES = 100_000
READ_MAX_LINES = 2000

# Syntax checks only run on files below this size; a giant generated file must
# still write successfully even if validation is skipped.
SYNTAX_MAX_CHARS = 200_000
_SYNTAX_TIMEOUT_S = 10.0


def _resolve(path: str, workspace: Path, home: Path | None) -> Path:
    return (
        resolve_agent_path(path, workspace, home=home)
        if home is not None
        else resolve_in_workspace(path, workspace)
    )


def _head_text(path: Path, text: str, *, offset: int = 0, limit: int = 0) -> str:
    lines = text.splitlines()
    if offset or limit:
        # Explicit range: return exactly the requested slice and label it. The
        # header is only added for ranged reads so default output stays identical.
        start = max(0, offset)
        end = start + limit if limit and limit > 0 else len(lines)
        shown = lines[start:end]
        if not shown:
            return f"[showing lines 0-0 of {len(lines)}]"
        return f"[showing lines {start + 1}-{start + len(shown)} of {len(lines)}]\n" + "\n".join(
            shown
        )
    if len(text) <= READ_MAX_BYTES and len(lines) <= READ_MAX_LINES:
        return text
    shown = lines[:READ_MAX_LINES]
    hidden = len(lines) - len(shown)
    hint = (
        f"[truncated: {len(lines)} lines, {len(text)} bytes; showing first "
        f"{len(shown)} lines ({hidden} hidden). Use edit_file or search_files to "
        "work with the rest.]"
    )
    return "\n".join(shown) + f"\n\n{hint}"


async def read_file(
    path: str,
    *,
    workspace: Path,
    home: Path | None = None,
    offset: int = 0,
    limit: int = 0,
) -> str:
    target = _resolve(path, workspace, home)
    text = await asyncio.to_thread(target.read_text, encoding="utf-8")
    return _head_text(target, text, offset=offset, limit=limit)


async def write_file(path: str, content: str, *, workspace: Path, home: Path | None = None) -> str:
    target = _resolve(path, workspace, home)
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_text, content, encoding="utf-8")
    out = f"wrote {target} ({len(content)} bytes)"
    note = await validate_syntax(target, content)
    return f"{out}\n{note}" if note else out


async def edit_file(
    path: str, old: str, new: str, *, workspace: Path, home: Path | None = None
) -> str:
    target = _resolve(path, workspace, home)
    text = await asyncio.to_thread(target.read_text, encoding="utf-8")
    if old not in text:
        raise ValueError("old_string not found")
    if text.count(old) > 1:
        raise ValueError("old_string not unique")
    updated = text.replace(old, new, 1)
    await asyncio.to_thread(target.write_text, updated, encoding="utf-8")
    out = f"edited {target}"
    note = await validate_syntax(target, updated)
    return f"{out}\n{note}" if note else out


async def validate_syntax(target: Path, content: str) -> str | None:
    """Best-effort syntax check for files just written; never raises.

    Saves the ``python3 -m py_compile`` / re-read round-trips seen while
    debugging a large generated script: the error is returned inline.
    """
    if len(content) > SYNTAX_MAX_CHARS:
        return None
    suffix = target.suffix.lower()
    try:
        if suffix == ".py":
            compile(content, str(target), "exec")
            return "compile: ok"
        if suffix in {".yaml", ".yml"} and target.parent.name == "tools":
            import yaml

            yaml.safe_load(content)
            return "yaml: ok"
        if suffix == ".js" and shutil.which("node"):
            return await _run_syntax_cmd(["node", "--check", str(target)])
        if suffix == ".sh" and shutil.which("bash"):
            return await _run_syntax_cmd(["bash", "-n", str(target)])
    except SyntaxError as exc:
        return f"compile error: line {exc.lineno}: {exc.msg}"
    except Exception as exc:  # yaml errors and anything unexpected
        return f"compile error: {exc}"
    return None


async def _run_syntax_cmd(cmd: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_SYNTAX_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        return "compile error: validation timed out"
    if proc.returncode == 0:
        return "compile: ok"
    detail = stderr.decode("utf-8", errors="replace").strip()
    return "compile error: " + (detail[:500] or f"exit={proc.returncode}")


SEARCH_MAX_MATCHES = 50


async def search_files(pattern: str, *, workspace: Path, glob: str = "**/*") -> str:
    """Find a literal substring across workspace files.

    ``pattern`` is a *literal substring* (not a regex). Content matches are
    returned as ``path:line: text``; a file whose relative path contains the
    pattern is returned as just its path. Results are capped.
    """
    if not pattern:
        return "(empty pattern)"
    workspace = workspace.resolve()

    def _search() -> list[str]:
        out: list[str] = []
        for path in workspace.glob(glob):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(workspace)
            except ValueError:
                continue
            # Name/path matches keep the filename-only form.
            if pattern in str(rel):
                out.append(str(rel))
                if len(out) >= SEARCH_MAX_MATCHES:
                    return out
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    out.append(f"{rel}:{lineno}: {line.strip()[:200]}")
                    if len(out) >= SEARCH_MAX_MATCHES:
                        return out
        return out

    matches = await asyncio.to_thread(_search)
    return "\n".join(matches) if matches else "(no matches)"
