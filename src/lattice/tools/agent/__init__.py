"""Pydantic-AI tool bindings — one module per CORE tool name."""

from __future__ import annotations

from typing import Any

from lattice.deps import CORE_TOOL_NAMES
from lattice.tools.agent._common import AgentT

from . import (
    shell,
    read_file,
    write_file,
    edit_file,
    search_files,
    ocr,
    web_search,
    web_fetch,
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
)

_MODULES = [
    shell,
    read_file,
    write_file,
    edit_file,
    search_files,
    ocr,
    web_search,
    web_fetch,
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


def register_all(agent: AgentT) -> dict[str, Any]:
    """Register every core tool on ``agent``; return policy-name → function map."""
    mapping: dict[str, Any] = {}
    for mod in _MODULES:
        mapping.update(mod.register(agent))
    missing = [n for n in CORE_TOOL_NAMES if n not in mapping]
    if missing:
        raise RuntimeError(f"tool registration missing: {missing}")
    return mapping
