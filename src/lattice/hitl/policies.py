"""HITL policy — only destructive tool actions require Approve/Deny.

Clarification choices go through ``clarify`` (not this gate).
"""

from __future__ import annotations

import re
from pathlib import Path

# Shell HITL only for high-blast-radius commands.
_SENSITIVE_ABS = (
    r"(?:etc|usr|bin|sbin|boot|System|Library|private|Applications|"
    r"dev/(?!null\b|zero\b|stdin\b|stdout\b|stderr\b|fd\b))"
)

DANGEROUS_SHELL_PATTERNS = (
    # Recursive ``rm`` is handled path-aware in ``_recursive_rm_needs_approval``:
    # cleanup confined to the workspace / authoring roots is routine; anything
    # reaching outside them (system paths, ``~``, ``..``, globs) still gates.
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
        "remove_path",
    }
)


# Recursive-delete handling. ``rm -rf`` is routine while authoring (cleaning a
# stray dir or build output), but the blast radius depends on the target, not the
# flag: deleting the workspace or a skill's own tree is fine, deleting ``/``,
# ``~``, ``..``, a glob, or an absolute path outside those roots is not.
_AUTHORING_HOME_DIRS = ("skills", "scripts", "tools")
_RM_INVOCATION = re.compile(r"\brm\b([^\n;|&]*)", re.I)
_RM_RECURSIVE_FLAG = re.compile(r"(?:^|\s)(?:-[a-zA-Z]*r[a-zA-Z]*|--recursive)(?=\s|$)")
_CD_INVOCATION = re.compile(r"(?:^|[;&|])\s*cd\s+([^\n;&|]+)")


def _rm_recursive_targets(command: str) -> list[list[str]]:
    invocations: list[list[str]] = []
    for match in _RM_INVOCATION.finditer(command):
        args = match.group(1)
        if not _RM_RECURSIVE_FLAG.search(args):
            continue
        invocations.append([t for t in args.split() if not t.startswith("-")])
    return invocations


def _effective_cwd(command: str, workspace: Path | None) -> Path | None:
    cwd = Path(workspace) if workspace else None
    for match in _CD_INVOCATION.finditer(command):
        raw = match.group(1).strip().strip("'\"")
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            cwd = candidate
        elif cwd is not None:
            cwd = cwd / candidate
    return cwd


def _safe_rm_roots(home: Path | None, workspace: Path | None) -> list[Path]:
    roots: list[Path] = []
    if workspace:
        roots.append(Path(workspace))
    if home:
        base = Path(home)
        roots.extend(base / name for name in _AUTHORING_HOME_DIRS)
    return [root.resolve() for root in roots]


def _recursive_rm_needs_approval(
    command: str, *, home: Path | None, workspace: Path | None
) -> bool:
    invocations = _rm_recursive_targets(command)
    if not invocations:
        return False
    cwd = _effective_cwd(command, workspace)
    roots = _safe_rm_roots(home, workspace)
    for targets in invocations:
        if not targets:
            return True
        for target in targets:
            if target.startswith(("~", "$HOME")) or "*" in target or target in {".", ".."}:
                return True
            path = Path(target)
            if path.is_absolute():
                resolved = path.resolve()
            elif cwd is not None:
                resolved = (cwd / path).resolve()
            else:
                resolved = None
            if resolved is None or not any(
                resolved == root or root in resolved.parents for root in roots
            ):
                return True
    return False


def shell_needs_approval(
    command: str, *, home: Path | None = None, workspace: Path | None = None
) -> bool:
    if _recursive_rm_needs_approval(command, home=home, workspace=workspace):
        return True
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


def tool_needs_approval(
    tool_name: str,
    *,
    args: dict | None = None,
    home: Path | None = None,
    workspace: Path | None = None,
) -> bool:
    if tool_name not in DESTRUCTIVE_GATE_TOOLS:
        return False
    if tool_name == "shell":
        if not args:
            return False
        return shell_needs_approval(str(args.get("command", "")), home=home, workspace=workspace)
    if tool_name == "sqlite_execute":
        if not args:
            return True
        return sql_needs_approval(str(args.get("sql", "")))
    if tool_name == "execute_script":
        if not args:
            return False
        body = str(args.get("code") or args.get("code_preview") or "")
        return script_needs_approval(body, language=str(args.get("language") or ""))
    if tool_name == "remove_path":
        if not args:
            return True
        # Any removal is gated; recursive removal always requires approval.
        return True
    # sqlite_unregister, profile_remove
    return True
