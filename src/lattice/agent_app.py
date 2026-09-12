"""Agent factory — deps, prompt helpers, and tool registration entrypoint."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset, FilteredToolset

from lattice.config import LatticeSettings, ToolTier
from lattice.deps import (  # noqa: F401 — re-export for existing imports
    CORE_TOOL_NAMES,
    TurnDeps,
    maybe_approve,
    traced,
    truncate_result,
)
from lattice.mcp import McpHostManager, should_defer_mcp
from lattice.mcp.toolset import McpToolset, mcp_tool_name
from lattice.memory import Memory, build_memory
from lattice.profiles import Profile, apply_name, merge_tool_policy, system_soul
from lattice.prompt import PromptBundle, build_skill_index_xml
from lattice.providers import build_openai_model
from lattice.tools.agent import build_toolsets, resolve_tier
from lattice.tools.user_tools import UserToolset, UserToolSpec

logger = logging.getLogger("lattice.agent_app")

# Tool definition bytes must stay stable across turns so provider-side cache
# prefixes survive; rebuilding toolsets per turn reorders MCP schemas. The cache
# is keyed by every input that changes the schema set and bounded so stale
# discovery cannot accumulate.
_TOOLSET_CACHE_MAX = 8
_toolset_cache: OrderedDict[tuple[Any, ...], AbstractToolset[TurnDeps]] = OrderedDict()


def clear_toolset_cache() -> None:
    _toolset_cache.clear()


def build_prompt_bundle(
    profile: Profile, skills_entries: list[tuple[str, str]], notices: list[str]
) -> PromptBundle:
    base = system_soul()
    persona = apply_name(profile.soul.strip(), profile.persona_name)
    identity = f"{base}\n\n{persona}".strip() if persona else base
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


def _toolset_cache_key(
    settings: LatticeSettings,
    mcp: McpHostManager,
    *,
    exclude: frozenset[str],
    filter_policy: bool,
    user_tools: Sequence[UserToolSpec] = (),
) -> tuple[Any, ...]:
    mcp_names = tuple(sorted(mcp_tool_name(i.server, i.name) for i in mcp.enabled_tools()))
    user_key = tuple(sorted(f"{spec.name}:{spec.digest}" for spec in user_tools))
    return (
        tuple(settings.tools.eager),
        tuple(settings.tools.cold),
        frozenset(exclude),
        mcp_names,
        str(settings.tools.mcp_defer),
        settings.tools.mcp_defer_threshold,
        filter_policy,
        user_key,
    )


def _cached_toolset(
    key: tuple[Any, ...], factory: Callable[[], AbstractToolset[TurnDeps]]
) -> AbstractToolset[TurnDeps]:
    cached = _toolset_cache.get(key)
    if cached is not None:
        _toolset_cache.move_to_end(key)
        return cached
    built = factory()
    _toolset_cache[key] = built
    _toolset_cache.move_to_end(key)
    while len(_toolset_cache) > _TOOLSET_CACHE_MAX:
        _toolset_cache.popitem(last=False)
    return built


def _user_tool_tier(settings: LatticeSettings, spec: UserToolSpec) -> ToolTier:
    """User tools default EAGER; ``tools.cold``/``tools.eager`` globs override."""
    return resolve_tier(
        spec.name,
        eager=settings.tools.eager,
        cold=settings.tools.cold,
        default=ToolTier.EAGER,
    )


def build_core_toolset(
    settings: LatticeSettings,
    mcp: McpHostManager,
    *,
    exclude: frozenset[str] = frozenset(),
    filter_policy: bool = True,
    user_tools: Sequence[UserToolSpec] = (),
) -> AbstractToolset[TurnDeps]:
    """Tiered core tools (eager + deferred cold), plus user tools and MCP tools.

    The built toolset is cached per schema signature so tool definitions recycle
    across turns instead of shifting the cacheable prefix. Safe because toolsets
    are stateless wrappers; per-request policy is still applied by
    ``filter_enabled`` at call time.
    """
    specs = list(user_tools)
    key = _toolset_cache_key(
        settings, mcp, exclude=exclude, filter_policy=filter_policy, user_tools=specs
    )

    def _build() -> AbstractToolset[TurnDeps]:
        from pydantic_ai.toolsets import DeferredLoadingToolset

        toolsets: list[AbstractToolset[TurnDeps]] = list(
            build_toolsets(
                exclude=exclude,
                eager=settings.tools.eager,
                cold=settings.tools.cold,
                defer_cold=True,
            )
        )
        if specs:
            eager_specs = [s for s in specs if _user_tool_tier(settings, s) is ToolTier.EAGER]
            cold_specs = [s for s in specs if _user_tool_tier(settings, s) is ToolTier.COLD]
            if eager_specs:
                toolsets.append(UserToolset(eager_specs))
            if cold_specs:
                toolsets.append(DeferredLoadingToolset(UserToolset(cold_specs)))
        if mcp.enabled_tools():
            mcp_toolset: AbstractToolset[TurnDeps] = McpToolset(mcp)
            if should_defer_mcp(mcp, settings.tools):
                mcp_toolset = DeferredLoadingToolset(mcp_toolset)
            toolsets.append(mcp_toolset)

        combined: AbstractToolset[TurnDeps] = (
            CombinedToolset(toolsets) if len(toolsets) > 1 else toolsets[0]
        )
        return filter_enabled(combined) if filter_policy else combined

    return _cached_toolset(key, _build)


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
    user_tools: Sequence[UserToolSpec] = (),
    model_settings: ModelSettings | None = None,
) -> Agent[TurnDeps, str]:
    from lattice.providers.settings import resolve_model_id

    primary = profile.primary_model or profile.model
    resolved = model or build_openai_model(
        settings, resolve_model_id(settings, profile_model=primary)
    )
    built = (
        list(toolsets)
        if toolsets is not None
        else [
            build_core_toolset(
                settings,
                mcp or McpHostManager(),
                exclude=exclude,
                user_tools=user_tools,
            )
        ]
    )
    return Agent(
        resolved,
        deps_type=TurnDeps,
        system_prompt=system_prompt,
        toolsets=built,
        capabilities=[tool_search_capability()],
        model_settings=model_settings,
    )


def resolve_enabled_tools(
    settings: LatticeSettings,
    profile: Profile,
    *,
    channel: str,
    mcp: McpHostManager,
    extra_tools: Sequence[str] = (),
) -> list[str]:
    channel_cfg = settings.telegram.tools if channel == "telegram" else settings.tools
    return merge_tool_policy(
        [*CORE_TOOL_NAMES, *extra_tools],
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
