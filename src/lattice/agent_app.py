"""Agent factory — deps, prompt helpers, and tool registration entrypoint."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent

from lattice.config import LatticeSettings
from lattice.deps import (  # noqa: F401 — re-export for existing imports
    CORE_TOOL_NAMES,
    TurnDeps,
    maybe_approve,
    traced,
    truncate_result,
)
from lattice.mcp import McpHostManager, should_defer_mcp
from lattice.memory import Memory, build_memory
from lattice.profiles import Profile, merge_tool_policy
from lattice.prompt import PromptBundle, build_skill_index_xml
from lattice.providers import build_openai_model
from lattice.tools.agent import register_all


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


def create_agent(
    settings: LatticeSettings,
    profile: Profile,
    *,
    system_prompt: str,
    model: Any | None = None,
) -> Agent[TurnDeps, str]:
    from lattice.providers.settings import resolve_model_id

    primary = profile.primary_model or profile.model
    resolved = model or build_openai_model(settings, resolve_model_id(settings, profile_model=primary))
    agent: Agent[TurnDeps, str] = Agent(resolved, deps_type=TurnDeps, system_prompt=system_prompt)
    agent.tool_functions = register_all(agent)  # type: ignore[attr-defined]
    return agent


def resolve_enabled_tools(
    settings: LatticeSettings,
    profile: Profile,
    *,
    channel: str,
    mcp: McpHostManager,
) -> list[str]:
    channel_cfg = settings.telegram.tools if channel == "telegram" else settings.tools
    names = list(CORE_TOOL_NAMES)
    if not should_defer_mcp(mcp, settings.tools) and not mcp.enabled_tools():
        names = [n for n in names if n not in {"tool_search", "tool_describe", "tool_invoke"}]
    elif not mcp.enabled_tools():
        names = [n for n in names if n not in {"tool_search", "tool_describe", "tool_invoke"}]
    return merge_tool_policy(
        names,
        profile_allow=profile.tools_allow,
        profile_deny=profile.tools_deny,
        channel_allow=channel_cfg.allow if hasattr(channel_cfg, "allow") else settings.tools.allow,
        channel_deny=channel_cfg.deny if hasattr(channel_cfg, "deny") else settings.tools.deny,
    )


def build_memory_for_profile(settings: LatticeSettings, profile: Profile) -> Memory:
    from lattice.providers.settings import apply_provider_env

    apply_provider_env(settings.provider)
    collection = profile.memory_collection or f"lattice-{profile.id}"
    return build_memory(collection=collection, path=settings.home / "qdrant")
