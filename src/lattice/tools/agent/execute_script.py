"""Agent tool: execute_script."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.deps import TurnDeps, maybe_approve, traced, truncate_result
from lattice.tools.agent._common import ToolsetT, ToolTier
from lattice.tools.file_safety import resolve_agent_path
from lattice.tools.script import execute_script as _execute_script
from lattice.tools.script import format_script_result

TIER = ToolTier.COLD


def _script_body_for_policy(
    *,
    code: str | None,
    path: str | None,
    workspace,
    home,
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


def register(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def execute_script(
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
        body = _script_body_for_policy(
            code=code,
            path=path,
            workspace=ctx.deps.workspace,
            home=ctx.deps.settings.home,
        )
        summary = path or f"inline {language} ({len(code or '')} chars)"
        denied = await maybe_approve(
            ctx,
            "execute_script",
            summary,
            language=language,
            path=path,
            code=body,
        )
        if denied:
            return denied

        async def _op() -> str:
            try:
                result = await _execute_script(
                    language=language,
                    code=code,
                    path=path,
                    timeout=timeout,
                    workspace=ctx.deps.workspace,
                    home=ctx.deps.settings.home,
                    cfg=ctx.deps.settings.scripts,
                    argv_extra=args,
                )
                return truncate_result(format_script_result(result))
            except Exception as exc:
                return f"execute_script error: {exc}"

        return await traced(
            ctx,
            "execute_script",
            {"language": language, "path": path, "timeout": timeout, "args": args},
            _op,
        )

    return {"execute_script": execute_script}
