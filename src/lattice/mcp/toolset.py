"""Adapter exposing discovered MCP tools as a pydantic-ai toolset."""

from __future__ import annotations

from typing import Any

from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator, core_schema

from lattice.deps import TurnDeps
from lattice.mcp.hosts import McpHostManager

# MCP tool schemas are authored by the server and passed through verbatim; the
# manager validates arguments, so the toolset accepts any object shape.
_ANY_ARGS_VALIDATOR = SchemaValidator(schema=core_schema.any_schema())


def mcp_tool_name(info_server: str, info_name: str) -> str:
    return f"{info_server}/{info_name}" if info_server else info_name


class McpToolset(AbstractToolset[TurnDeps]):
    """Expose the host manager's discovered tools to the model.

    ``McpHostManager`` discovers tools; this toolset surfaces them and routes
    calls back to the manager. No live session exists yet, so calls return the
    manager's not-connected notice.
    """

    def __init__(self, manager: McpHostManager, *, id: str | None = None) -> None:
        self.manager = manager
        self._id = id

    @property
    def id(self) -> str | None:
        return self._id

    async def get_tools(self, ctx: Any) -> dict[str, ToolsetTool[TurnDeps]]:
        out: dict[str, ToolsetTool[TurnDeps]] = {}
        # Sort by stable name so MCP tool schemas are byte-identical across
        # discovery orders, preserving provider-side cache prefixes.
        for info in sorted(
            self.manager.enabled_tools(), key=lambda i: mcp_tool_name(i.server, i.name)
        ):
            name = mcp_tool_name(info.server, info.name)
            out[name] = ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=name,
                    description=info.description,
                    parameters_json_schema=info.schema
                    or {"type": "object", "properties": {}},
                ),
                max_retries=ctx.max_retries,
                args_validator=_ANY_ARGS_VALIDATOR,
            )
        return out

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: Any, tool: ToolsetTool[TurnDeps]
    ) -> Any:
        return self.manager.call(name, tool_args)
