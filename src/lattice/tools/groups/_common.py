"""Shared types for the grouped built-in tool registry.

Each group module declares a :class:`ToolGroup` of :class:`ToolBinding`s. A
binding owns one tool body and its default tier; a :class:`NamespacedToolset`
renders the group's tools under the model-facing ``group__leaf`` wire name while
calls and tracing stay canonical (``group/leaf``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai.toolsets import FunctionToolset, WrapperToolset

from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.tools.middleware import ToolPolicy

ToolsetT = FunctionToolset[TurnDeps]
RegisterFn = Callable[[ToolsetT], dict[str, Any]]


@dataclass(frozen=True)
class ToolBinding:
    """One tool body: its trimmed leaf name, default tier, registrar, and gate."""

    leaf: str
    tier: ToolTier
    register: RegisterFn
    policy: ToolPolicy | None = None


@dataclass
class ToolGroup:
    """A namespace of related tools that share a wire-name prefix."""

    name: str
    description: str
    bindings: tuple[ToolBinding, ...]

    def canonical_names(self) -> list[str]:
        return [f"{self.name}/{binding.leaf}" for binding in self.bindings]

    def binding(self, leaf: str) -> ToolBinding | None:
        for item in self.bindings:
            if item.leaf == leaf:
                return item
        return None


@dataclass
class NamespacedToolset(WrapperToolset[TurnDeps]):
    """Render a group's tools under ``{group}__{leaf}`` wire names.

    Mirrors pydantic-ai's ``PrefixedToolset`` but joins with ``__`` so the result
    satisfies the provider function-name grammar. ``call_tool`` maps the wire name
    back to the leaf, restores the canonical ``group/leaf`` name on the run
    context, and delegates unchanged to the wrapped toolset.
    """

    group: str = ""

    @property
    def tool_name_conflict_hint(self) -> str:
        return "Change the group name to avoid tool name conflicts."

    @property
    def tools(self) -> dict[str, Any]:
        """Model-facing ``wire name -> Tool`` view for introspection/tests."""
        return {
            f"{self.group}__{name}": tool
            for name, tool in getattr(self.wrapped, "tools", {}).items()
        }

    async def get_tools(self, ctx: Any) -> dict[str, Any]:
        return {
            new_name: replace(
                tool,
                toolset=self,
                tool_def=replace(tool.tool_def, name=new_name),
            )
            for name, tool in (await super().get_tools(ctx)).items()
            if (new_name := f"{self.group}__{name}")
        }

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: Any, tool: Any) -> Any:
        leaf = name.removeprefix(f"{self.group}__")
        canonical = f"{self.group}/{leaf}"
        ctx = replace(ctx, tool_name=canonical)
        tool = replace(tool, tool_def=replace(tool.tool_def, name=leaf))
        return await super().call_tool(leaf, tool_args, ctx, tool)


__all__ = ["NamespacedToolset", "RegisterFn", "ToolBinding", "ToolGroup", "ToolsetT"]
