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

# First path segment → writable under lattice_home (channel authoring)
HOME_PREFIXES = frozenset({"skills", "profiles"})


class PathDeniedError(PermissionError):
    pass


def is_denied_path(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    parts = {p.lower() for p in resolved.parts}
    name = resolved.name.lower()
    if any(part in parts or part in name for part in DENY_NAME_PARTS):
        return True
    return any(name.endswith(suf) for suf in DENY_SUFFIXES)


def _assert_not_denied(resolved: Path) -> None:
    if is_denied_path(resolved):
        raise PathDeniedError(f"path denied: {resolved}")


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
    _assert_not_denied(resolved)
    return resolved


def resolve_agent_path(path: str | Path, workspace: Path, *, home: Path) -> Path:
    """Resolve a tool path: workspace jail, or lattice_home skills/ / profiles/."""
    home = home.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    raw = Path(path).expanduser()

    if not raw.is_absolute() and raw.parts and raw.parts[0] in HOME_PREFIXES:
        root = home / raw.parts[0]
        resolved = (home / raw).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PathDeniedError(f"path outside {raw.parts[0]}/: {resolved}") from exc
        _assert_not_denied(resolved)
        return resolved

    if raw.is_absolute():
        resolved = raw.resolve()
        for root in (workspace, home / "skills", home / "profiles"):
            try:
                resolved.relative_to(root.resolve())
                _assert_not_denied(resolved)
                return resolved
            except ValueError:
                continue
        raise PathDeniedError(f"path outside workspace/skills/profiles: {resolved}")

    return resolve_in_workspace(path, workspace)
