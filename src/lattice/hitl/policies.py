"""HITL policy helpers — which tools need approval."""

from __future__ import annotations

import re

DANGEROUS_SHELL_PATTERNS = (
    re.compile(r"\brm\b", re.I),
    re.compile(r"\bsudo\b", re.I),
    re.compile(r"\bmkfs\b", re.I),
    re.compile(r">\s*/", re.I),
    re.compile(r"\bchmod\s+[0-7]*7", re.I),
)

ALWAYS_GATE_TOOLS = frozenset(
    {
        "shell",
        "write_file",
        "edit_file",
        "sqlite_execute",
        "sqlite_register",
        "sqlite_unregister",
    }
)


def shell_needs_approval(command: str) -> bool:
    return any(p.search(command) for p in DANGEROUS_SHELL_PATTERNS)


def tool_needs_approval(tool_name: str, *, args: dict | None = None) -> bool:
    if tool_name in ALWAYS_GATE_TOOLS:
        if tool_name == "shell" and args:
            return shell_needs_approval(str(args.get("command", "")))
        return tool_name != "shell"
    return False
