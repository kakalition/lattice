"""Agent helpers (primary / secondary)."""

from lattice.agents.secondary import (
    SECONDARY_SYSTEM_PROMPT,
    SECONDARY_TOOL_NAMES,
    WORKER_SYSTEM_PROMPT,
    clear_secondary_agent_cache,
    get_secondary_agent,
    run_secondary,
    run_worker,
)

__all__ = [
    "SECONDARY_SYSTEM_PROMPT",
    "SECONDARY_TOOL_NAMES",
    "WORKER_SYSTEM_PROMPT",
    "clear_secondary_agent_cache",
    "get_secondary_agent",
    "run_secondary",
    "run_worker",
]
