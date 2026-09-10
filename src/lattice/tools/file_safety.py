"""Workspace path jail and secret path denies."""

from __future__ import annotations

from pathlib import Path

DENY_SUFFIXES = (
    ".env",
    ".pem",
    ".key",
)
DENY_NAME_PARTS = (
    ".ssh",
    ".gnupg",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "state.db",
)


class PathDeniedError(PermissionError):
    pass


def is_denied_path(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    parts = {p.lower() for p in resolved.parts}
    name = resolved.name.lower()
    if any(part in parts or part in name for part in DENY_NAME_PARTS):
        return True
    return any(name.endswith(suf) for suf in DENY_SUFFIXES)


def resolve_in_workspace(path: str | Path, workspace: Path) -> Path:
    workspace = workspace.expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise PathDeniedError(f"path outside workspace: {resolved}") from exc
    if is_denied_path(resolved):
        raise PathDeniedError(f"path denied: {resolved}")
    return resolved
