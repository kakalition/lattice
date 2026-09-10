"""Profile listing and sticky channel→profile map (via session store)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from lattice.paths import lattice_home
from lattice.profiles.load import Profile, ensure_default_profile, load_profile
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


def remove_profile(profile_id: str, home: Path | None = None) -> Path:
    """Delete profiles/<id>/ under lattice home. Refuses `default` and unknown ids."""
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
