"""Grouped built-in tool registry and toolset builders.

``GROUPS`` is the fixed, ordered namespace registry. ``build_toolsets`` emits one
eager and/or deferred toolset per group, every eager toolset ahead of every
deferred one, so a tool revealed by search is a pure suffix of the request's
tools array and the cached prefix only grows.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.toolsets import AbstractToolset, DeferredLoadingToolset, FunctionToolset

from lattice.config import ToolTier
from lattice.tool_names import (
    CORE_TOOL_NAMES,
    DEFAULT_TIERS,
    canonical_name,
    matches,
)
from lattice.tools.groups import (
    browser,
    compute,
    files,
    interaction,
    media,
    memory,
    profiles,
    schedule,
    skills,
    sqlite,
    web,
)
from lattice.tools.groups._common import NamespacedToolset, ToolGroup, ToolsetT
from lattice.tools.middleware import ToolPolicy

__all__ = [
    "CORE_POLICIES",
    "CORE_TOOL_NAMES",
    "GROUPS",
    "ToolGroup",
    "ToolTier",
    "build_toolsets",
    "default_eager_names",
    "resolve_tier",
    "tool_functions",
]

GROUPS: tuple[ToolGroup, ...] = (
    files.GROUP,
    media.GROUP,
    web.GROUP,
    browser.GROUP,
    compute.GROUP,
    interaction.GROUP,
    schedule.GROUP,
    memory.GROUP,
    sqlite.GROUP,
    skills.GROUP,
    profiles.GROUP,
)

# Per-tool gates (precheck/needs/summary) applied uniformly by GuardedToolset.
CORE_POLICIES: dict[str, ToolPolicy] = {
    f"{group.name}/{binding.leaf}": binding.policy
    for group in GROUPS
    for binding in group.bindings
    if binding.policy is not None
}

_REGISTERED: frozenset[str] = frozenset(
    f"{group.name}/{binding.leaf}" for group in GROUPS for binding in group.bindings
)


def _excluded(name: str, leaf: str, exclude: frozenset[str]) -> bool:
    return name in exclude or leaf in exclude


def resolve_tier(
    name: str, *, eager: list[str], cold: list[str], default: ToolTier | None = None
) -> ToolTier:
    """Config globs override the tool default; ``cold`` wins on conflict.

    Patterns are normalized (legacy ``sqlite_*`` globs and flat names) before
    matching the canonical name. ``default`` is used for names with no default
    tier (e.g. runtime user tools); otherwise ``DEFAULT_TIERS`` applies.
    """
    canonical = canonical_name(name)
    if matches(canonical, cold):
        return ToolTier.COLD
    if matches(canonical, eager):
        return ToolTier.EAGER
    if default is not None:
        return default
    return DEFAULT_TIERS[canonical]


def default_eager_names() -> list[str]:
    """Canonical names that ship eagerly with no config override."""
    return [name for name, tier in DEFAULT_TIERS.items() if tier is ToolTier.EAGER]


def tool_functions(*, exclude: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Canonical-name → function map for every registered tool (tests/introspection)."""
    mapping: dict[str, Any] = {}
    for group in GROUPS:
        for binding in group.bindings:
            canonical = f"{group.name}/{binding.leaf}"
            if _excluded(canonical, binding.leaf, exclude):
                continue
            toolset: ToolsetT = FunctionToolset()
            for leaf, fn in binding.register(toolset).items():
                mapping[f"{group.name}/{leaf}"] = fn
    _check_complete(exclude=exclude)
    return mapping


def _check_complete(*, exclude: frozenset[str]) -> None:
    missing = [name for name in CORE_TOOL_NAMES if name not in _REGISTERED and name not in exclude]
    if missing:
        raise RuntimeError(f"tool registration missing: {missing}")


def build_toolsets(
    *,
    exclude: frozenset[str] = frozenset(),
    eager: list[str] | None = None,
    cold: list[str] | None = None,
    defer_cold: bool = True,
) -> list[AbstractToolset[Any]]:
    """Build per-group eager and (optionally deferred) cold toolsets.

    Every eager group toolset is emitted before every deferred one. ``exclude``
    names (canonical or leaf) are not registered at all. ``eager``/``cold`` are
    fnmatch globs that override each tool's default tier.
    """
    eager_globs = list(eager or [])
    cold_globs = list(cold or [])

    eager_toolsets: list[AbstractToolset[Any]] = []
    cold_toolsets: list[AbstractToolset[Any]] = []

    for group in GROUPS:
        eager_ts: ToolsetT = FunctionToolset()
        cold_ts: ToolsetT = FunctionToolset()
        for binding in group.bindings:
            canonical = f"{group.name}/{binding.leaf}"
            if _excluded(canonical, binding.leaf, exclude):
                continue
            tier = resolve_tier(canonical, eager=eager_globs, cold=cold_globs, default=binding.tier)
            target = cold_ts if tier is ToolTier.COLD else eager_ts
            binding.register(target)
        if eager_ts.tools:
            eager_toolsets.append(NamespacedToolset(group=group.name, wrapped=eager_ts))
        if cold_ts.tools:
            cold_toolsets.append(NamespacedToolset(group=group.name, wrapped=cold_ts))

    _check_complete(exclude=exclude)

    deferred: list[AbstractToolset[Any]] = (
        [DeferredLoadingToolset(ts) for ts in cold_toolsets] if defer_cold else cold_toolsets
    )
    return [*eager_toolsets, *deferred]
