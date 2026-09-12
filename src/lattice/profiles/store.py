"""Profile listing and sticky channel→profile map (via session store)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from lattice.paths import lattice_home
from lattice.profiles.load import (
    DEFAULT_NAME,
    DEFAULT_PROFILE_SOUL,
    NAME_LINE_RE,
    Profile,
    ensure_default_profile,
    load_profile,
    parse_persona,
)
from lattice.session import SessionStore

_PROFILE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def validate_profile_id(profile_id: str) -> str:
    pid = (profile_id or "").strip()
    if not _PROFILE_ID_RE.fullmatch(pid):
        raise ValueError(
            "invalid profile id (use letters, digits, _ or -, max 64, no path separators)"
        )
    return pid


def list_profiles(home: Path | None = None) -> list[str]:
    root = (home or lattice_home()) / "profiles"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "profile.yaml").exists())


def _profile_removal_target(profile_id: str, home: Path | None) -> tuple[str, Path]:
    pid = validate_profile_id(profile_id)
    if pid == "default":
        raise ValueError("cannot remove the default profile")
    root_home = home or lattice_home()
    profiles_root = (root_home / "profiles").resolve()
    target = (profiles_root / pid).resolve()
    try:
        target.relative_to(profiles_root)
    except ValueError as exc:
        raise ValueError("invalid profile path") from exc
    if not target.is_dir() or not (target / "profile.yaml").is_file():
        raise FileNotFoundError(f"profile not found: {pid}")
    return pid, target


def validate_removable_profile(profile_id: str, home: Path | None = None) -> str | None:
    """Return a user-facing error if a profile cannot be removed, else ``None``.

    Used before HITL so an invalid/unremovable id never triggers a prompt.
    """
    try:
        _profile_removal_target(profile_id, home)
    except (ValueError, FileNotFoundError) as exc:
        return f"error: {exc}"
    return None


def remove_profile(profile_id: str, home: Path | None = None) -> Path:
    """Delete profiles/<id>/ under lattice home. Refuses `default` and unknown ids."""
    _, target = _profile_removal_target(profile_id, home)
    shutil.rmtree(target)
    return target


async def resolve_sticky_profile(
    store: SessionStore,
    *,
    channel: str,
    user_id: str,
    fallback: str = "default",
) -> str:
    sticky = await store.get_sticky_profile(channel, user_id)
    return sticky or fallback


def get_profile(profile_id: str, home: Path | None = None) -> Profile:
    ensure_default_profile(home)
    return load_profile(profile_id, home)


def soul_path(profile_id: str, home: Path | None = None) -> Path:
    """Path to ``profiles/<id>/SOUL.md`` for a validated profile id."""
    pid = validate_profile_id(profile_id)
    return (home or lattice_home()) / "profiles" / pid / "SOUL.md"


def _write_profile_file(
    profile_id: str, filename: str, text: str, *, empty_error: str, home: Path | None
) -> Path:
    pid = validate_profile_id(profile_id)
    body = (text or "").strip()
    if not body:
        raise ValueError(empty_error)
    if pid == "default":
        ensure_default_profile(home)
    root = (home or lattice_home()) / "profiles" / pid
    if not (root / "profile.yaml").is_file():
        raise FileNotFoundError(f"profile not found: {pid}")
    path = root / filename
    path.write_text(body + "\n", encoding="utf-8")
    return path


def read_soul(profile_id: str, home: Path | None = None) -> str:
    path = soul_path(profile_id, home)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def read_soul_name(profile_id: str, home: Path | None = None) -> str:
    """The persona name declared in a profile's SOUL.md (default ``Lattice``)."""
    return parse_persona(read_soul(profile_id, home) or DEFAULT_PROFILE_SOUL)[0]


def write_soul(profile_id: str, text: str, home: Path | None = None) -> Path:
    """Replace a profile's persona SOUL.md. Live on the next turn (no restart).

    A ``name:`` line is preserved if the new text does not declare one.
    """
    body = (text or "").strip()
    if not body:
        raise ValueError("soul text is required")
    if not NAME_LINE_RE.search(body):
        body = f"name: {read_soul_name(profile_id, home)}\n\n{body}"
    return _write_profile_file(
        profile_id, "SOUL.md", body, empty_error="soul text is required", home=home
    )


def write_soul_name(profile_id: str, name: str, home: Path | None = None) -> Path:
    """Set just the persona name, keeping the rest of SOUL.md."""
    clean = (name or "").strip().replace("\n", " ")
    if not clean:
        raise ValueError("name is required")
    _, persona = parse_persona(read_soul(profile_id, home) or DEFAULT_PROFILE_SOUL)
    return write_soul(profile_id, f"name: {clean}\n\n{persona}\n", home=home)


def reset_soul_name(profile_id: str, home: Path | None = None) -> Path:
    return write_soul_name(profile_id, DEFAULT_NAME, home=home)


def reset_soul(profile_id: str, home: Path | None = None) -> Path:
    return write_soul(profile_id, DEFAULT_PROFILE_SOUL, home=home)
