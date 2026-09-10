"""Profile listing and sticky channel→profile map (via session store)."""

from __future__ import annotations

from pathlib import Path

from lattice.paths import lattice_home
from lattice.profiles.load import Profile, ensure_default_profile, load_profile
from lattice.session import SessionStore


def list_profiles(home: Path | None = None) -> list[str]:
    root = (home or lattice_home()) / "profiles"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "profile.yaml").exists())


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
