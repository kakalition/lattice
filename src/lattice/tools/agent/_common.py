"""Shared types for agent tool bindings.

Each binding module exposes ``register(toolset) -> {policy_name: fn}`` plus a
``TIER`` constant. Tool visibility is enforced centrally by a
``FilteredToolset`` over ``ctx.deps.enabled_tools``, so tool bodies no longer
carry their own allow checks.
"""

from __future__ import annotations

from pydantic_ai.toolsets import FunctionToolset

from lattice.config import ToolTier
from lattice.deps import TurnDeps

ToolsetT = FunctionToolset[TurnDeps]

__all__ = ["ToolTier", "ToolsetT"]
