"""Pydantic-AI tool bindings — one module per CORE tool name.

Each module registers exactly one tool under its policy name and declares a
default ``TIER``. This package partitions the tools into an eager toolset (sent
on every request) and a cold toolset (deferred behind native tool search).
"""

from __future__ import annotations

import fnmatch
from typing import Any

from pydantic_ai.toolsets import AbstractToolset, DeferredLoadingToolset, FunctionToolset

from lattice.config import ToolTier
from lattice.deps import CORE_TOOL_NAMES
from lattice.tools.agent._common import ToolsetT

from . import (
    browser_interact,
    browser_snapshot,
    calculator,
    clarify,
    edit_file,
    execute_script,
    generate_chart,
    generate_pdf,
    memory_add,
    memory_forget,
    memory_search,
    memory_update,
    ocr,
    profile_list,
    profile_remove,
    read_file,
    remove_path,
    schedule_add,
    schedule_cancel,
    schedule_list,
    search_files,
    session_search,
    shell,
    skill_view,
    skills_list,
    sqlite_backup,
    sqlite_execute,
    sqlite_list,
    sqlite_query,
    sqlite_register,
    sqlite_schema,
    sqlite_unregister,
    timezone_get,
    timezone_set,
    todo,
    web_fetch,
    web_search,
    write_file,
)

_MODULES = [
    shell,
    read_file,
    write_file,
    edit_file,
    remove_path,
    search_files,
    ocr,
    generate_pdf,
    generate_chart,
    web_search,
    web_fetch,
    browser_interact,
    browser_snapshot,
    execute_script,
    clarify,
    calculator,
    todo,
    schedule_add,
    schedule_list,
    schedule_cancel,
    timezone_get,
    timezone_set,
    session_search,
    memory_search,
    memory_add,
    memory_update,
    memory_forget,
    sqlite_list,
    sqlite_schema,
    sqlite_query,
    sqlite_execute,
    sqlite_register,
    sqlite_unregister,
    sqlite_backup,
    skills_list,
    skill_view,
    profile_list,
    profile_remove,
]


def _module_tool_name(mod: Any) -> str:
    """Each binding module is named after the single policy tool it registers."""
    return mod.__name__.rpartition(".")[2]


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


def resolve_tier(
    name: str, *, eager: list[str], cold: list[str], default: ToolTier | None = None
) -> ToolTier:
    """Config globs override the module default; ``cold`` wins on conflict.

    ``default`` is used for names with no module (e.g. runtime user tools); when
    omitted, the module's declared ``TIER`` is used.
    """
    if _matches(name, cold):
        return ToolTier.COLD
    if _matches(name, eager):
        return ToolTier.EAGER
    if default is not None:
        return default
    return _default_tiers()[name]


def _default_tiers() -> dict[str, ToolTier]:
    return {_module_tool_name(mod): mod.TIER for mod in _MODULES}


def default_eager_names() -> list[str]:
    """Tool names that ship eagerly with no config override."""
    return [n for n, tier in _default_tiers().items() if tier is ToolTier.EAGER]


def tool_functions(*, exclude: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Policy-name → function map for every registered tool (tests/introspection)."""
    mapping: dict[str, Any] = {}
    for mod in _MODULES:
        name = _module_tool_name(mod)
        if name in exclude:
            continue
        toolset: ToolsetT = FunctionToolset()
        mapping.update(mod.register(toolset))
    _check_complete(mapping, exclude=exclude)
    return mapping


def _check_complete(mapping: dict[str, Any], *, exclude: frozenset[str]) -> None:
    missing = [n for n in CORE_TOOL_NAMES if n not in mapping and n not in exclude]
    if missing:
        raise RuntimeError(f"tool registration missing: {missing}")


def build_toolsets(
    *,
    exclude: frozenset[str] = frozenset(),
    eager: list[str] | None = None,
    cold: list[str] | None = None,
    defer_cold: bool = True,
) -> list[AbstractToolset[Any]]:
    """Build the eager toolset and (optionally deferred) cold toolset.

    ``exclude`` names tools that must not be registered at all. ``eager``/``cold``
    are fnmatch globs that override each module's default tier.
    """
    eager_globs = list(eager or [])
    cold_globs = list(cold or [])

    eager_toolset: ToolsetT = FunctionToolset()
    cold_toolset: ToolsetT = FunctionToolset()
    seen: dict[str, Any] = {}

    for mod in _MODULES:
        name = _module_tool_name(mod)
        if name in exclude:
            continue
        target = (
            cold_toolset
            if resolve_tier(name, eager=eager_globs, cold=cold_globs) is ToolTier.COLD
            else eager_toolset
        )
        seen.update(mod.register(target))

    _check_complete(seen, exclude=exclude)

    out: list[AbstractToolset[Any]] = [eager_toolset]
    if cold_toolset.tools:
        out.append(DeferredLoadingToolset(cold_toolset) if defer_cold else cold_toolset)
    return out
