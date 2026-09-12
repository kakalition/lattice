"""Pydantic-AI tool bindings — one module per CORE tool name."""

from __future__ import annotations

from typing import Any

from lattice.deps import CORE_TOOL_NAMES
from lattice.tools.agent._common import AgentT

from . import (
    browser_interact,
    browser_snapshot,
    clarify,
    delegate,
    edit_file,
    execute_script,
    generate_chart,
    generate_pdf,
    memory_add,
    memory_forget,
    memory_search,
    memory_update,
    metric_log,
    metric_query,
    ocr,
    profile_list,
    profile_remove,
    read_file,
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
    tool_describe,
    tool_invoke,
    tool_search,
    web_fetch,
    web_search,
    write_file,
)

_MODULES = [
    shell,
    read_file,
    write_file,
    edit_file,
    search_files,
    ocr,
    generate_pdf,
    generate_chart,
    web_search,
    web_fetch,
    browser_interact,
    browser_snapshot,
    metric_log,
    metric_query,
    execute_script,
    clarify,
    todo,
    delegate,
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
    tool_search,
    tool_describe,
    tool_invoke,
]


def _module_tool_name(mod: Any) -> str:
    """Each binding module is named after the single policy tool it registers."""
    return mod.__name__.rpartition(".")[2]


def register_all(agent: AgentT, *, exclude: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Register every core tool on ``agent``; return policy-name → function map.

    ``exclude`` names tools that must not be registered at all (the ``@agent.tool``
    decorator registers eagerly, so excluded modules are skipped before calling).
    """
    mapping: dict[str, Any] = {}
    for mod in _MODULES:
        if _module_tool_name(mod) in exclude:
            continue
        mapping.update(mod.register(agent))
    missing = [n for n in CORE_TOOL_NAMES if n not in mapping and n not in exclude]
    if missing:
        raise RuntimeError(f"tool registration missing: {missing}")
    return mapping
