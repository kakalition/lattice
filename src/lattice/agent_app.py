"""Agent factory — deps, prompt helpers, and tool registration entrypoint."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset, FilteredToolset

from lattice.config import LatticeSettings
from lattice.deps import (  # noqa: F401 — re-export for existing imports
    CORE_TOOL_NAMES,
    TurnDeps,
    maybe_approve,
    traced,
    truncate_result,
)
from lattice.mcp import McpHostManager, should_defer_mcp
from lattice.mcp.toolset import McpToolset
from lattice.memory import Memory, build_memory
from lattice.profiles import Profile, merge_tool_policy
from lattice.prompt import PromptBundle, build_skill_index_xml
from lattice.providers import build_openai_model
from lattice.tools.agent import build_toolsets

logger = logging.getLogger("lattice.agent_app")


def build_prompt_bundle(
    profile: Profile, skills_entries: list[tuple[str, str]], notices: list[str]
) -> PromptBundle:
    identity = profile.soul.strip() or "You are Lattice."
    context = profile.user_notes.strip()
    return PromptBundle(
        identity=identity,
        context=context,
        skill_index=build_skill_index_xml(skills_entries),
        notices=notices,
    )


def filter_enabled(toolset: AbstractToolset[TurnDeps]) -> AbstractToolset[TurnDeps]:
    """Hide tools the current turn's policy did not enable.

    Filters per request (not at construction) so one Agent stays reusable across
    profiles and channels. Replaces the old in-tool ``not_allowed`` check: a
    disallowed tool is absent from the schema instead of erroring when called.
    """

    def _keep(ctx: Any, tool_def: Any) -> bool:
        enabled = getattr(ctx.deps, "enabled_tools", None) or []
        return tool_def.name in enabled

    return FilteredToolset(toolset, _keep)


def build_core_toolset(
    settings: LatticeSettings,
    mcp: McpHostManager,
    *,
    exclude: frozenset[str] = frozenset(),
    filter_policy: bool = True,
) -> AbstractToolset[TurnDeps]:
    """Tiered core tools (eager + deferred cold), plus discovered MCP tools."""
    toolsets: list[AbstractToolset[TurnDeps]] = list(
        build_toolsets(
            exclude=exclude,
            eager=settings.tools.eager,
            cold=settings.tools.cold,
            defer_cold=True,
        )
    )
    if mcp.enabled_tools():
        mcp_toolset: AbstractToolset[TurnDeps] = McpToolset(mcp)
        if should_defer_mcp(mcp, settings.tools):
            from pydantic_ai.toolsets import DeferredLoadingToolset

            mcp_toolset = DeferredLoadingToolset(mcp_toolset)
        toolsets.append(mcp_toolset)

    combined: AbstractToolset[TurnDeps] = (
        CombinedToolset(toolsets) if len(toolsets) > 1 else toolsets[0]
    )
    return filter_enabled(combined) if filter_policy else combined


def tool_search_capability() -> Any:
    """Force the local ``search_tools`` fallback.

    The capability is auto-injected, but on providers without a native tool-search
    surface (OpenAI chat completions) the default strategy emits no discovery tool
    at all, leaving deferred tools unreachable. Pinning ``keywords`` guarantees a
    callable ``search_tools`` function on every provider.
    """
    return ToolSearch(strategy="keywords")


def create_agent(
    settings: LatticeSettings,
    profile: Profile,
    *,
    system_prompt: str,
    model: Any | None = None,
    mcp: McpHostManager | None = None,
    exclude: frozenset[str] = frozenset(),
    toolsets: Sequence[AbstractToolset[TurnDeps]] | None = None,
) -> Agent[TurnDeps, str]:
    from lattice.providers.settings import resolve_model_id

    primary = profile.primary_model or profile.model
    resolved = model or build_openai_model(
        settings, resolve_model_id(settings, profile_model=primary)
    )
    built = (
        list(toolsets)
        if toolsets is not None
        else [build_core_toolset(settings, mcp or McpHostManager(), exclude=exclude)]
    )
    return Agent(
        resolved,
        deps_type=TurnDeps,
        system_prompt=system_prompt,
        toolsets=built,
        capabilities=[tool_search_capability()],
    )


def resolve_enabled_tools(
    settings: LatticeSettings,
    profile: Profile,
    *,
    channel: str,
    mcp: McpHostManager,
) -> list[str]:
    channel_cfg = settings.telegram.tools if channel == "telegram" else settings.tools
    return merge_tool_policy(
        list(CORE_TOOL_NAMES),
        profile_allow=profile.tools_allow,
        profile_deny=profile.tools_deny,
        channel_allow=channel_cfg.allow if hasattr(channel_cfg, "allow") else settings.tools.allow,
        channel_deny=channel_cfg.deny if hasattr(channel_cfg, "deny") else settings.tools.deny,
    )


def build_memory_for_profile(settings: LatticeSettings, profile: Profile) -> Memory:
    from lattice.providers.settings import apply_provider_env, auxiliary_model_name

    apply_provider_env(settings.provider)
    collection = profile.memory_collection or f"lattice-{profile.id}"
    # mem0 runs an LLM to extract memories; keep it on the configured auxiliary
    # model instead of mem0's built-in gpt-4o-mini default, and let reasoning
    # models use their own parameter set (mem0 only auto-detects o1/o3/gpt-5).
    return build_memory(
        collection=collection,
        path=settings.home / "qdrant",
        llm_model=auxiliary_model_name(settings, profile_aux=profile.auxiliary_model),
        is_reasoning_model=settings.memory.is_reasoning_model,
    )


def verify_memory_for_profile(settings: LatticeSettings, profile: Profile) -> list[str]:
    """Boot-time memory health check. Returns notes; raises ``RuntimeError`` on failure.

    Guards a silent failure mode: memory search once returned zero hits forever
    because a swallowed exception hid a mem0 signature mismatch. A round-trip
    probe turns that into a loud failure at startup.
    """
    from lattice.memory import probe_memory
    from lattice.profiles import get_profile

    profile = get_profile(profile.id, settings.home)
    collection = profile.memory_collection or f"lattice-{profile.id}"
    memory = build_memory_for_profile(settings, profile)
    logger.info("memory self-check: probing collection %s", collection)
    probe_memory(memory, collection=collection)
    return [f"memory self-check: ok ({collection})"]
