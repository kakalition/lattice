"""Built-in ``compute`` group — sandboxed scripts and arithmetic."""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai import RunContext

from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.hitl.policies import script_needs_approval
from lattice.tools.calculator import calculate
from lattice.tools.file_safety import resolve_agent_path
from lattice.tools.groups._common import ToolBinding, ToolGroup, ToolsetT
from lattice.tools.middleware import ToolPolicy
from lattice.tools.script import execute_script as _execute_script
from lattice.tools.script import format_script_result

_GROUP = "compute"


def _script_body_for_policy(
    *,
    code: str | None,
    path: str | None,
    workspace: Any,
    home: Any,
) -> str:
    if code and code.strip():
        return code
    if not path:
        return ""
    try:
        target = resolve_agent_path(path, workspace, home=home)
        if target.is_file():
            return target.read_text(encoding="utf-8", errors="replace")[:50_000]
    except Exception:
        return path
    return path


def _script_needs(ctx: RunContext[TurnDeps], args: dict[str, Any]) -> bool:
    body = _script_body_for_policy(
        code=args.get("code"),
        path=args.get("path"),
        workspace=ctx.deps.workspace,
        home=ctx.deps.settings.home,
    )
    return script_needs_approval(body, language=str(args.get("language") or ""))


_SCRIPT_POLICY = ToolPolicy(
    needs=_script_needs,
    summary=lambda _ctx, args: str(args.get("path") or f"inline {args.get('language', '')}"),
)


def register_script(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def script(
        ctx: RunContext[TurnDeps],
        language: str,
        code: str | None = None,
        path: str | None = None,
        timeout: float = 60.0,
        args: list[str] | None = None,
    ) -> str:
        """Run a sandboxed local script (python/node/bash) via bwrap when available.

        Prefer path under scripts/ for reusable files, or pass inline code.
        ``args`` are passed to the script as command-line argv.
        HITL only for dangerous patterns (subprocess/rm/network/eval/…).
        Network stays off unless scripts.allow_network is true.
        """
        try:
            env_extra: dict[str, str] = {
                "LATTICE_SQLITE_ROW_LIMIT": str(ctx.deps.settings.sqlite.query_row_limit),
            }
            allow = ctx.deps.profile.sqlite_allow
            if allow is not None:
                # The sqlite-admin script enforces the same profile allowlist
                # as the native sqlite_* tools; without this it would see all.
                env_extra["LATTICE_SQLITE_ALLOW"] = json.dumps(allow)
            result = await _execute_script(
                language=language,
                code=code,
                path=path,
                timeout=timeout,
                workspace=ctx.deps.workspace,
                home=ctx.deps.settings.home,
                cfg=ctx.deps.settings.scripts,
                argv_extra=args,
                env_extra=env_extra,
            )
            # `traced` owns truncation (head+tail with a scratch reference) so
            # a trailing traceback on stderr survives.
            return format_script_result(result)
        except Exception as exc:
            return f"error: execute_script: {exc}"

    return {"script": script}


def register_calculator(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def calculator(ctx: RunContext[TurnDeps], expression: str) -> str:
        """Evaluate a math expression safely (arithmetic, parentheses, powers,
        and functions like sqrt/sin/log). Example: "(3 + 4) * 2"."""
        return calculate(expression)

    return {"calculator": calculator}


GROUP = ToolGroup(
    name=_GROUP,
    description="Compute: sandboxed scripts and safe arithmetic.",
    bindings=(
        ToolBinding("script", ToolTier.COLD, register_script, _SCRIPT_POLICY),
        ToolBinding("calculator", ToolTier.EAGER, register_calculator),
    ),
)
