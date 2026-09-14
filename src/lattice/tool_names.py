"""Canonical built-in tool names and the canonical⇄wire mapping.

Built-in tools have a grouped, MCP-shaped identity:

- **Canonical** ``group/leaf`` (e.g. ``sqlite/execute``) is the operator-facing
  spelling: ``tools.allow/deny/eager/cold``, profile policy, HITL gates, audit,
  stats, the action ledger, and this registry.
- **Wire** ``group__leaf`` (e.g. ``sqlite__execute``) is what the model sees and
  calls. Providers reject ``/`` in function names (OpenAI requires
  ``^[a-zA-Z0-9_-]{1,64}$``), so the separator is doubled.

``__`` never appears inside a group or leaf segment, so the two forms map
bijectively. Flat pre-group names (``sqlite_execute``) and legacy ``prefix_*``
globs are normalized to canonical on load so existing config keeps working.

This module is deliberately dependency-free (it imports only ``ToolTier``) so
``lattice.deps`` can import it without a cycle.
"""

from __future__ import annotations

import fnmatch
import re

from lattice.config import ToolTier

# Provider function-name grammar (OpenAI): ^[a-zA-Z0-9_-]{1,64}$.
WIRE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
GROUP_NAME_RE = re.compile(r"^[a-z][a-z0-9]*$")
LEAF_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_WIRE_SEP = "__"

GROUP_NAMES: tuple[str, ...] = (
    "files",
    "media",
    "web",
    "browser",
    "compute",
    "interaction",
    "schedule",
    "memory",
    "sqlite",
    "skills",
    "profiles",
)

# Canonical name -> default tier, in fixed group order. The order here is the
# order tools are offered to the model, so it must stay stable for cache bytes.
DEFAULT_TIERS: dict[str, ToolTier] = {
    "files/shell": ToolTier.EAGER,
    "files/read": ToolTier.EAGER,
    "files/write": ToolTier.EAGER,
    "files/edit": ToolTier.EAGER,
    "files/remove": ToolTier.EAGER,
    "files/search": ToolTier.EAGER,
    "media/ocr": ToolTier.EAGER,
    "media/pdf": ToolTier.COLD,
    "media/chart": ToolTier.COLD,
    "web/search": ToolTier.EAGER,
    "web/fetch": ToolTier.EAGER,
    "browser/interact": ToolTier.COLD,
    "browser/snapshot": ToolTier.COLD,
    "compute/script": ToolTier.COLD,
    "compute/calculator": ToolTier.EAGER,
    "interaction/clarify": ToolTier.EAGER,
    "interaction/todo": ToolTier.EAGER,
    "schedule/add": ToolTier.EAGER,
    "schedule/list": ToolTier.COLD,
    "schedule/cancel": ToolTier.COLD,
    "schedule/timezone_get": ToolTier.COLD,
    "schedule/timezone_set": ToolTier.COLD,
    "memory/session_search": ToolTier.EAGER,
    "memory/search": ToolTier.EAGER,
    "memory/add": ToolTier.EAGER,
    "memory/update": ToolTier.COLD,
    "memory/forget": ToolTier.COLD,
    "sqlite/list": ToolTier.COLD,
    "sqlite/schema": ToolTier.EAGER,
    "sqlite/query": ToolTier.EAGER,
    "sqlite/execute": ToolTier.COLD,
    "sqlite/register": ToolTier.COLD,
    "sqlite/unregister": ToolTier.COLD,
    "sqlite/backup": ToolTier.COLD,
    "skills/list": ToolTier.EAGER,
    "skills/view": ToolTier.EAGER,
    "profiles/list": ToolTier.COLD,
    "profiles/remove": ToolTier.COLD,
}

CORE_TOOL_NAMES: list[str] = list(DEFAULT_TIERS)

# Every pre-group flat tool name -> its canonical grouped name.
LEGACY_ALIASES: dict[str, str] = {
    "shell": "files/shell",
    "read_file": "files/read",
    "write_file": "files/write",
    "edit_file": "files/edit",
    "remove_path": "files/remove",
    "search_files": "files/search",
    "ocr": "media/ocr",
    "generate_pdf": "media/pdf",
    "generate_chart": "media/chart",
    "web_search": "web/search",
    "web_fetch": "web/fetch",
    "browser_interact": "browser/interact",
    "browser_snapshot": "browser/snapshot",
    "execute_script": "compute/script",
    "calculator": "compute/calculator",
    "clarify": "interaction/clarify",
    "todo": "interaction/todo",
    "schedule_add": "schedule/add",
    "schedule_list": "schedule/list",
    "schedule_cancel": "schedule/cancel",
    "timezone_get": "schedule/timezone_get",
    "timezone_set": "schedule/timezone_set",
    "session_search": "memory/session_search",
    "memory_search": "memory/search",
    "memory_add": "memory/add",
    "memory_update": "memory/update",
    "memory_forget": "memory/forget",
    "sqlite_list": "sqlite/list",
    "sqlite_schema": "sqlite/schema",
    "sqlite_query": "sqlite/query",
    "sqlite_execute": "sqlite/execute",
    "sqlite_register": "sqlite/register",
    "sqlite_unregister": "sqlite/unregister",
    "sqlite_backup": "sqlite/backup",
    "skills_list": "skills/list",
    "skill_view": "skills/view",
    "profile_list": "profiles/list",
    "profile_remove": "profiles/remove",
}

# Legacy ``prefix_*`` policy globs -> the canonical group glob they meant.
LEGACY_GLOB_ALIASES: dict[str, str] = {
    "sqlite_*": "sqlite/*",
    "web_*": "web/*",
    "browser_*": "browser/*",
    "memory_*": "memory/*",
    "schedule_*": "schedule/*",
    "timezone_*": "schedule/*",
    "profile_*": "profiles/*",
    "skill*": "skills/*",
    "generate_*": "media/*",
    "session_*": "memory/*",
}

RESERVED_GROUPS: frozenset[str] = frozenset({*GROUP_NAMES, "user", "mcp"})
RESERVED_LEAVES: frozenset[str] = frozenset(
    {name.split("/", 1)[1] for name in CORE_TOOL_NAMES} | {"search_tools"}
)

_CANONICAL: frozenset[str] = frozenset(CORE_TOOL_NAMES)


def wire_name(canonical: str) -> str:
    """``sqlite/execute`` -> the model-facing ``sqlite__execute``."""
    return canonical.replace("/", _WIRE_SEP)


def canonical_name(name: str) -> str:
    """Normalize a wire, canonical, or legacy flat name to canonical.

    Unknown names pass through unchanged so user/MCP identities survive.
    """
    if not name:
        return name
    if name in _CANONICAL:
        return name
    if _WIRE_SEP in name:
        return name.replace(_WIRE_SEP, "/", 1)
    if "/" in name:
        return name
    return LEGACY_ALIASES.get(name, name)


# ``normalize_name`` is the policy-facing spelling of the same operation.
normalize_name = canonical_name


def normalize_pattern(pattern: str) -> str:
    """Normalize an exact legacy name or ``prefix_*`` glob to canonical form."""
    if not pattern:
        return pattern
    if pattern in LEGACY_ALIASES:
        return LEGACY_ALIASES[pattern]
    if pattern in LEGACY_GLOB_ALIASES:
        return LEGACY_GLOB_ALIASES[pattern]
    return pattern


def leaf_of(canonical: str) -> str:
    normalized = canonical_name(canonical)
    return normalized.split("/", 1)[1] if "/" in normalized else normalized


def group_of(canonical: str) -> str:
    normalized = canonical_name(canonical)
    return normalized.split("/", 1)[0] if "/" in normalized else ""


def matches(name: str, patterns: list[str]) -> bool:
    """fnmatch a canonical name against policy globs (legacy globs normalized)."""
    canonical = canonical_name(name)
    return any(fnmatch.fnmatch(canonical, normalize_pattern(pat)) for pat in patterns)


__all__ = [
    "CORE_TOOL_NAMES",
    "DEFAULT_TIERS",
    "GROUP_NAMES",
    "GROUP_NAME_RE",
    "LEAF_NAME_RE",
    "LEGACY_ALIASES",
    "LEGACY_GLOB_ALIASES",
    "RESERVED_GROUPS",
    "RESERVED_LEAVES",
    "WIRE_NAME_RE",
    "canonical_name",
    "group_of",
    "leaf_of",
    "matches",
    "normalize_name",
    "normalize_pattern",
    "wire_name",
]
