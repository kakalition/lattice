"""Prompt-cache capability detection and OpenRouter cache settings.

Capability facts come from the model profile (populated by ``OpenRouterProvider``
from the downstream provider), never from inline provider checks.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.models.openrouter import OpenRouterModelSettings

from lattice.config import LatticeSettings


def _profile(model: Any) -> dict[str, Any] | None:
    profile = getattr(model, "profile", None)
    return profile if isinstance(profile, dict) else None


def supports_explicit_cache(model: Any) -> bool:
    """True when the model accepts explicit ``CachePoint`` breakpoints."""
    profile = _profile(model)
    if profile is None:
        return False
    return bool(profile.get("openrouter_supports_cache_control", False))


def prompt_cache_settings(settings: LatticeSettings, model: Any) -> OpenRouterModelSettings:
    """Build typed OpenRouter cache settings for ``model``.

    Returns an empty mapping when caching is disabled or the model profile does
    not support explicit cache control, so non-OpenRouter endpoints are untouched.
    """
    if not settings.agent.prompt_cache:
        return {}
    profile = _profile(model)
    if profile is None or not profile.get("openrouter_supports_cache_control", False):
        return {}
    ttl = settings.agent.prompt_cache_ttl
    cache: OpenRouterModelSettings = {"openrouter_cache_instructions": ttl}
    if profile.get("openrouter_supports_tool_cache", False):
        cache["openrouter_cache_tool_definitions"] = ttl
    return cache
