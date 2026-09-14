"""One middleware seam for every tool path: built-in, user, and MCP.

``GuardedToolset`` wraps the combined toolset and applies the same pipeline to
every call regardless of where the tool came from:

1. **precheck** — tool-specific validation that must run before any prompt
   (e.g. "this removal is out of jail"), returning an error string to short-circuit;
2. **approval** — ``approve_tool`` with a per-tool policy (or the built-in
   ``tool_needs_approval`` default);
3. **execution** — ``traced`` for start/end events, truncation, and the
   repeated-failure breaker.

Tool bodies stay pure. Policy is resolved per canonical name so an external MCP
tool and an internal built-in run through identical machinery.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import WrapperToolset

from lattice.deps import TurnDeps, approve_tool, traced
from lattice.hitl import tool_needs_approval
from lattice.tool_names import canonical_name

Policy = Callable[[RunContext[TurnDeps], dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolPolicy:
    """Per-tool gate: optional precheck, approval need, and approval summary.

    All callables receive the run context and raw tool args. Missing pieces fall
    back to the generic built-in policy (``tool_needs_approval`` + derived summary).
    """

    precheck: Policy | None = None
    needs: Policy | None = None
    summary: Policy | None = None


DEFAULT_POLICY = ToolPolicy()
PolicyResolver = Callable[[RunContext[TurnDeps], str], ToolPolicy]


def default_needs(ctx: RunContext[TurnDeps], canonical: str, args: dict[str, Any]) -> bool:
    return tool_needs_approval(
        canonical,
        args=args,
        home=ctx.deps.settings.home,
        workspace=ctx.deps.workspace,
    )


def generic_summary(canonical: str, args: dict[str, Any]) -> str:
    for key in ("command", "path", "name", "query", "url", "sql", "profile_id", "expression"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value[:200]
    if args:
        return ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])[:200]
    return canonical


@dataclass
class GuardedToolset(WrapperToolset[TurnDeps]):
    """Apply precheck → approval → traced execution uniformly to every tool."""

    resolve: PolicyResolver | None = None

    def _policy(self, ctx: RunContext[TurnDeps], canonical: str) -> ToolPolicy:
        if self.resolve is None:
            return DEFAULT_POLICY
        return self.resolve(ctx, canonical) or DEFAULT_POLICY

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[TurnDeps], tool: Any
    ) -> Any:
        canonical = canonical_name(name)
        policy = self._policy(ctx, canonical)

        if policy.precheck is not None:
            error = policy.precheck(ctx, tool_args)
            if error is not None:
                return error

        needs = (
            bool(policy.needs(ctx, tool_args))
            if policy.needs is not None
            else default_needs(ctx, canonical, tool_args)
        )
        if needs:
            summary = (
                str(policy.summary(ctx, tool_args))
                if policy.summary is not None
                else generic_summary(canonical, tool_args)
            )
            # ``needs`` is already resolved (policy-level or default); pass it so
            # ``approve_tool`` does not re-derive it from the name alone.
            denied = await approve_tool(ctx, canonical, summary, tool_args, needs=True)
            if denied:
                return denied

        return await traced(
            ctx,
            canonical,
            tool_args,
            lambda: WrapperToolset.call_tool(self, name, tool_args, ctx, tool),
        )


__all__ = [
    "DEFAULT_POLICY",
    "GuardedToolset",
    "Policy",
    "PolicyResolver",
    "ToolPolicy",
    "default_needs",
    "generic_summary",
]
