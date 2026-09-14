"""Agent factory — deps, prompt helpers, and tool registration entrypoint."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable, Sequence
from functools import partial
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
from lattice.providers.logging_model import with_llm_logging
from lattice.tool_names import canonical_name, matches, normalize_pattern, wire_name
from lattice.tools.groups import CORE_POLICIES, build_toolsets, resolve_tier
from lattice.tools.middleware import DEFAULT_POLICY, GuardedToolset, ToolPolicy
from lattice.tools.user_tools import UserToolset, UserToolSpec, user_tool_policy

logger = logging.getLogger("lattice.agent_app")

# Framework default guidance for the ``search_tools`` description. Imported from
# pydantic-ai so wording tracks upstream; a local fallback keeps us working if the
# private path moves (behaviour degrades to our guidance instead of crashing).
try:  # pragma: no cover - exercised implicitly by the import
    from pydantic_ai.toolsets._tool_search import (
        _DEFAULT_TOOL_DESCRIPTION as _BASE_SEARCH_DESCRIPTION,
    )
except ImportError:  # pragma: no cover
    _BASE_SEARCH_DESCRIPTION = (
        "Search first for a standalone deferred tool when current tools and catalog"
        " descriptions do not name the requested operation. A capability id used as an"
        " ordinary domain word does not request that capability. This cannot find"
        " capability-owned tools; load a listed capability by id instead. If no tools"
        " are found, do not retry."
    )

# Cap the enumerated cold-tool manifest so the description cannot grow unbounded.
_MANIFEST_MAX_NAMES = 30

# Tool definition bytes must stay stable across turns so provider-side cache
# prefixes survive; rebuilding toolsets per turn reorders MCP schemas. The cache
# is keyed by every input that changes the schema set and bounded so stale
# discovery cannot accumulate.
_TOOLSET_CACHE_MAX = 8
_toolset_cache: OrderedDict[tuple[Any, ...], AbstractToolset[TurnDeps]] = OrderedDict()


def clear_toolset_cache() -> None:
    _toolset_cache.clear()


def build_prompt_bundle(
    profile: Profile,
    skills_entries: list[tuple[str, str]],
    notices: list[str],
    *,
    runtime_context: str = "",
) -> PromptBundle:
    base = system_soul()
    persona = apply_name(profile.soul.strip(), profile.persona_name)
    identity = f"{base}\n\n{persona}".strip() if persona else base
    # Invariant runtime facts join user notes in the cacheable prefix; only the
    # volatile clock/ledger stay in ``notices``.
    context = "\n\n".join(p for p in (profile.user_notes.strip(), runtime_context.strip()) if p)
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
        # Tool definitions carry wire names (``group__leaf``); policy is canonical.
        return canonical_name(tool_def.name) in enabled

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
    # Normalize legacy globs so equivalent configs share one cached toolset.
    return (
        tuple(normalize_pattern(p) for p in settings.tools.eager),
        tuple(normalize_pattern(p) for p in settings.tools.cold),
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
    """User tools default EAGER; ``tools.cold``/``tools.eager`` globs override.

    The canonical name is ``user/<name>``; a bare legacy pattern still matches so
    existing configs keep working.
    """
    canonical = f"user/{spec.name}"
    cold = settings.tools.cold
    eager = settings.tools.eager
    if matches(canonical, cold) or matches(spec.name, cold):
        return ToolTier.COLD
    if matches(canonical, eager) or matches(spec.name, eager):
        return ToolTier.EAGER
    return ToolTier.EAGER


def resolve_tool_policy(ctx: Any, canonical: str) -> ToolPolicy:
    """Gate policy for any tool: core registry, then user tools, then default.

    Called by ``GuardedToolset`` for every call, so built-in, user, and MCP tools
    all pass through the same precheck/approval path.
    """
    policy = CORE_POLICIES.get(canonical)
    if policy is not None:
        return policy
    if canonical.startswith("user/"):
        return user_tool_policy(ctx, canonical)
    return DEFAULT_POLICY


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

        # ``build_toolsets`` returns every eager group toolset before every
        # deferred one. Keep that split so an eager user/MCP toolset appended
        # below still precedes all deferred work: a revealed tool is then a pure
        # suffix of the request's tools array and the cached prefix only grows.
        built = list(
            build_toolsets(
                exclude=exclude,
                eager=settings.tools.eager,
                cold=settings.tools.cold,
                defer_cold=True,
            )
        )
        eager: list[AbstractToolset[TurnDeps]] = [
            ts for ts in built if not isinstance(ts, DeferredLoadingToolset)
        ]
        deferred: list[AbstractToolset[TurnDeps]] = [
            ts for ts in built if isinstance(ts, DeferredLoadingToolset)
        ]

        if specs:
            eager_specs = [s for s in specs if _user_tool_tier(settings, s) is ToolTier.EAGER]
            cold_specs = [s for s in specs if _user_tool_tier(settings, s) is ToolTier.COLD]
            if eager_specs:
                eager.append(UserToolset(eager_specs))
            if cold_specs:
                deferred.append(DeferredLoadingToolset(UserToolset(cold_specs)))
        if mcp.enabled_tools():
            mcp_toolset: AbstractToolset[TurnDeps] = McpToolset(mcp)
            if should_defer_mcp(mcp, settings.tools):
                deferred.append(DeferredLoadingToolset(mcp_toolset))
            else:
                eager.append(mcp_toolset)

        toolsets = eager + deferred
        combined: AbstractToolset[TurnDeps] = (
            CombinedToolset(toolsets) if len(toolsets) > 1 else toolsets[0]
        )
        inner = filter_enabled(combined) if filter_policy else combined
        # One middleware seam for every tool source: precheck → approval → traced.
        return GuardedToolset(wrapped=inner, resolve=resolve_tool_policy)

    return _cached_toolset(key, _build)


def build_search_description(
    enabled: Sequence[str],
    settings: LatticeSettings,
    mcp: McpHostManager,
    user_tools: Sequence[UserToolSpec] = (),
) -> str:
    """Model-facing description for ``search_tools``, listing discoverable cold tools.

    Enumerating the enabled cold *core* names (sorted for byte stability) tells the
    model what discovery can reach without a speculative search. MCP/user tools
    contribute a generic phrase only, so per-server churn cannot change the bytes.
    Policy-denied tools are absent because ``enabled`` is already filtered.
    """
    cold_core = sorted(
        name
        for name in enabled
        if name in CORE_TOOL_NAMES
        and resolve_tier(name, eager=settings.tools.eager, cold=settings.tools.cold)
        is ToolTier.COLD
    )
    if not cold_core:
        return _BASE_SEARCH_DESCRIPTION

    shown = cold_core[:_MANIFEST_MAX_NAMES]
    listing = ", ".join(wire_name(name) for name in shown)
    if len(cold_core) > _MANIFEST_MAX_NAMES:
        listing = f"{listing} (+{len(cold_core) - _MANIFEST_MAX_NAMES} more)"

    parts = [f"{_BASE_SEARCH_DESCRIPTION} Deferred tools available via search_tools: {listing}."]
    if mcp.enabled_tools() and should_defer_mcp(mcp, settings.tools):
        parts.append("Also deferred MCP tools.")
    if any(_user_tool_tier(settings, spec) is ToolTier.COLD for spec in user_tools):
        parts.append("Also deferred user tools.")
    return " ".join(parts)


def tool_search_capability(
    strategy: str = "bm25",
    *,
    tool_description: str | None = None,
    min_ratio: float = 0.35,
) -> Any:
    """Force a local ``search_tools`` fallback with the chosen ranking algorithm.

    The capability is auto-injected, but on providers without a native tool-search
    surface (OpenAI chat completions) the default strategy emits no discovery tool
    at all, leaving deferred tools unreachable. Pinning a *local* strategy
    guarantees a callable ``search_tools`` on every provider.

    ``bm25`` (default) plugs our in-process BM25 scorer in as a callable. The
    provider-native ``"bm25"`` string must never be passed to ``ToolSearch`` — that
    commits to Anthropic's server-side strategy and raises on OpenAI-compatible
    models. ``keywords`` keeps pydantic-ai's built-in overlap algorithm; unknown
    values fall back to ``bm25``.

    ``tool_description`` replaces the shipped guidance (the cold-tool manifest);
    ``min_ratio`` trims weak BM25 matches relative to the top score.
    """
    if strategy == "keywords":
        return ToolSearch(strategy="keywords", tool_description=tool_description)
    from lattice.tool_search import bm25_search_fn

    return ToolSearch(
        strategy=partial(bm25_search_fn, min_ratio=min_ratio),
        tool_description=tool_description,
    )


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
    search_description: str | None = None,
) -> Agent[TurnDeps, str]:
    from lattice.providers.settings import resolve_model_id

    primary = profile.primary_model or profile.model
    resolved = model or build_openai_model(
        settings, resolve_model_id(settings, profile_model=primary)
    )
    resolved = with_llm_logging(resolved)
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
        capabilities=[
            tool_search_capability(
                settings.tools.search_strategy,
                tool_description=search_description,
                min_ratio=settings.tools.search_min_ratio,
            )
        ],
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
    universe = [*CORE_TOOL_NAMES, *(f"user/{name}" for name in extra_tools)]
    return merge_tool_policy(
        universe,
        profile_allow=profile.tools_allow,
        profile_deny=profile.tools_deny,
        channel_allow=channel_cfg.allow if hasattr(channel_cfg, "allow") else settings.tools.allow,
        channel_deny=channel_cfg.deny if hasattr(channel_cfg, "deny") else settings.tools.deny,
    )


def build_memory_for_profile(
    settings: LatticeSettings, profile: Profile, *, model_id: str | None = None
) -> Memory:
    from lattice.providers.settings import apply_provider_env, resolve_model_id

    apply_provider_env(settings.provider)
    collection = profile.memory_collection or f"lattice-{profile.id}"
    # mem0 runs an LLM to extract memories; keep it on the active primary model
    # instead of mem0's built-in gpt-4o-mini default, and let reasoning models use
    # their own parameter set (mem0 only auto-detects o1/o3/gpt-5).
    resolved = model_id or resolve_model_id(
        settings, profile_model=profile.primary_model or profile.model
    )
    return build_memory(
        collection=collection,
        path=settings.home / "qdrant",
        llm_model=resolved,
        is_reasoning_model=settings.memory.is_reasoning_model,
        extract_on_turn=settings.memory.extract_on_turn,
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
