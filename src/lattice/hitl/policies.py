"""HITL policy — only destructive tool actions require Approve/Deny.

Clarification choices go through ``clarify`` (not this gate).
"""

from __future__ import annotations

import re

# Shell HITL only for high-blast-radius commands.
_SENSITIVE_ABS = (
    r"(?:etc|usr|bin|sbin|boot|System|Library|private|Applications|"
    r"dev/(?!null\b|zero\b|stdin\b|stdout\b|stderr\b|fd\b))"
)

DANGEROUS_SHELL_PATTERNS = (
    re.compile(r"\brm\s+(?:-[a-zA-Z]*r[a-zA-Z]*\b|--recursive\b)", re.I),
    re.compile(r"\brm\b[^\n;|&]*(?:\s/(?:\s|$)|(?:\$HOME|~)(?:/|\s|$))", re.I),
    re.compile(r"\b(?:sudo|doas)\b", re.I),
    re.compile(r"\b(?:mkfs(?:\.\w+)?|fdisk|diskutil\s+erase)\b", re.I),
    re.compile(r"\bdd\b[^\n;|&]*\bof=/dev/", re.I),
    re.compile(r"\b(?:shutdown|reboot|halt|poweroff)\b", re.I),
    re.compile(
        rf"(?:(?<![0-9])>\s*|tee\s+(?:-a\s+)?)\/{_SENSITIVE_ABS}\b",
        re.I,
    ),
    re.compile(r"\b(?:curl|wget)\b[^\n;|&]*\|\s*(?:ba)?sh\b", re.I),
    re.compile(r"\b(?:chmod|chown)\b[^\n;|&]*\s/(?:\s|$)", re.I),
)

# SQL that destroys or restructures data. Everyday DML (INSERT/UPDATE, including
# upserts and row-level DELETE) does not gate; only unrecoverable/structural ops do.
_CATASTROPHIC_SQL_PATTERNS = (
    re.compile(r"\b(?:DROP|ALTER|TRUNCATE)\b", re.I),
    re.compile(r"\bATTACH\b", re.I),
    # DELETE without a WHERE clause wipes the whole table.
    re.compile(r"\bDELETE\s+FROM\b(?![^;]*\bWHERE\b)", re.I | re.S),
)

# execute_script HITL — high-blast-radius ops / host escape / network / eval.
DANGEROUS_SCRIPT_PATTERNS = (
    re.compile(r"\bos\.system\b"),
    re.compile(r"\bsubprocess\b"),
    re.compile(r"\bshutil\.rmtree\b"),
    re.compile(r"\bos\.(?:remove|unlink|rmdir)\b"),
    re.compile(r"\b(?:eval|exec)\s*\("),
    re.compile(r"\bctypes\b"),
    re.compile(r"\bchild_process\b"),
    re.compile(r"\bfs\.(?:rmSync|rmdirSync|promises\.rm)\b"),
    re.compile(r"\b(?:urllib|requests|httpx|aiohttp)\b"),
    re.compile(r"\bsocket\.(?:socket|create_connection)\b"),
    re.compile(r"\bfetch\s*\("),
    re.compile(r"\bhttps?\.(?:get|request)\b"),
    re.compile(r"""(?:open|readFile(?:Sync)?)\s*\(\s*['"]/(?:etc|private|System|usr)\b"""),
    re.compile(r"\b(?:curl|wget)\b", re.I),
)

# Tools that may need Approve/Deny — still filtered by args below.
DESTRUCTIVE_GATE_TOOLS = frozenset(
    {
        "shell",
        "sqlite_execute",
        "sqlite_unregister",
        "profile_remove",
        "execute_script",
    }
)


def shell_needs_approval(command: str) -> bool:
    return any(p.search(command) for p in DANGEROUS_SHELL_PATTERNS)


def _strip_sql_literals(sql: str) -> str:
    """Remove string/identifier literals so keywords inside data don't trigger the gate.

    e.g. ``UPDATE t SET name = 'ALTER EGO'`` is ordinary DML, not an ``ALTER``.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"`":  # string or quoted identifier
            quote = ch
            i += 1
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:  # doubled = escaped
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(" ")
            continue
        if ch == "-" and sql.startswith("--", i):  # line comment
            end = sql.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
            continue
        if ch == "/" and sql.startswith("/*", i):  # block comment
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def sql_needs_approval(sql: str) -> bool:
    return any(p.search(_strip_sql_literals(sql)) for p in _CATASTROPHIC_SQL_PATTERNS)


def script_needs_approval(code: str, *, language: str | None = None) -> bool:
    """True when script body looks destructive / escapes the sandbox intent."""
    body = code or ""
    if not body.strip():
        return False
    if shell_needs_approval(body):
        return True
    return any(p.search(body) for p in DANGEROUS_SCRIPT_PATTERNS)


def tool_needs_approval(tool_name: str, *, args: dict | None = None) -> bool:
    if tool_name not in DESTRUCTIVE_GATE_TOOLS:
        return False
    if tool_name == "shell":
        if not args:
            return False
        return shell_needs_approval(str(args.get("command", "")))
    if tool_name == "sqlite_execute":
        if not args:
            return True
        return sql_needs_approval(str(args.get("sql", "")))
    if tool_name == "execute_script":
        if not args:
            return False
        body = str(args.get("code") or args.get("code_preview") or "")
        return script_needs_approval(body, language=str(args.get("language") or ""))
    # sqlite_unregister, profile_remove
    return True
