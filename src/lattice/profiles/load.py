"""Profile loading — SOUL/USER + tool/skill/sqlite policy."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lattice.paths import lattice_home


@dataclass
class Profile:
    id: str
    description: str = ""
    soul: str = ""
    user_notes: str = ""
    skills_prefer: list[str] = field(default_factory=list)
    skills_disable: list[str] = field(default_factory=list)
    tools_allow: list[str] = field(default_factory=lambda: ["*"])
    tools_deny: list[str] = field(default_factory=list)
    sqlite_allow: list[str] | None = None
    memory_collection: str | None = None
    model: str | None = None  # legacy alias for primary_model
    primary_model: str | None = None
    secondary_model: str | None = None
    auxiliary_model: str | None = None
    workspace: Path | None = None
    root: Path | None = None


def _match_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


def merge_tool_policy(
    tool_names: list[str],
    *,
    profile_allow: list[str],
    profile_deny: list[str],
    channel_allow: list[str],
    channel_deny: list[str],
) -> list[str]:
    """Deny wins: (universe ∩ profile.allow − profile.deny) ∩ channel.allow − channel.deny."""
    out: list[str] = []
    for name in tool_names:
        if not _match_any(name, profile_allow):
            continue
        if _match_any(name, profile_deny):
            continue
        if not _match_any(name, channel_allow):
            continue
        if _match_any(name, channel_deny):
            continue
        out.append(name)
    return out


def load_profile(profile_id: str, home: Path | None = None) -> Profile:
    root = (home or lattice_home()) / "profiles" / profile_id
    if not root.is_dir():
        raise FileNotFoundError(f"profile not found: {profile_id}")
    data: dict[str, Any] = {}
    yaml_path = root / "profile.yaml"
    if yaml_path.is_file():
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    soul = ""
    soul_path = root / "SOUL.md"
    if soul_path.is_file():
        soul = soul_path.read_text(encoding="utf-8")
    user_notes = ""
    user_path = root / "USER.md"
    if user_path.is_file():
        user_notes = user_path.read_text(encoding="utf-8")
    skills = data.get("skills") or {}
    tools = data.get("tools") or {}
    sqlite = data.get("sqlite") or {}
    memory = data.get("memory") or {}
    workspace = data.get("workspace") or (data.get("agent") or {}).get("workspace")
    return Profile(
        id=profile_id,
        description=str(data.get("description") or ""),
        soul=soul,
        user_notes=user_notes,
        skills_prefer=list(skills.get("prefer") or []),
        skills_disable=list(skills.get("disable") or []),
        tools_allow=list(tools.get("allow") or ["*"]),
        tools_deny=list(tools.get("deny") or []),
        sqlite_allow=list(sqlite["allow"]) if "allow" in sqlite else None,
        memory_collection=memory.get("collection"),
        model=data.get("primary_model") or data.get("model"),
        primary_model=data.get("primary_model") or data.get("model"),
        secondary_model=data.get("secondary_model"),
        auxiliary_model=data.get("auxiliary_model"),
        workspace=Path(workspace).expanduser() if workspace else None,
        root=root,
    )


DEFAULT_SOUL = """\
You are Lattice, a careful personal assistant.
Be concise, honest, and tool-aware. Prefer read-only actions before writes.
Use clarify when requirements are ambiguous. Respect HITL denials.
"""

DEFAULT_PROFILE_YAML = """\
name: default
description: Default Lattice profile
skills:
  prefer: [telegram-chat, skill-authoring, profile-authoring, session-hygiene, safe-shell, web-research, sqlite-admin, cited-research, weekly-review]
tools:
  allow: ["*"]
  deny: []
memory:
  collection: lattice-default
"""


def ensure_default_profile(home: Path | None = None) -> Path:
    root = (home or lattice_home()) / "profiles" / "default"
    root.mkdir(parents=True, exist_ok=True)
    yaml_path = root / "profile.yaml"
    if not yaml_path.exists():
        yaml_path.write_text(DEFAULT_PROFILE_YAML, encoding="utf-8")
    soul_path = root / "SOUL.md"
    if not soul_path.exists():
        soul_path.write_text(DEFAULT_SOUL, encoding="utf-8")
    user_path = root / "USER.md"
    if not user_path.exists():
        user_path.write_text("# User notes\n", encoding="utf-8")
    return root
