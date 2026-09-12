"""Provider settings helpers."""

from __future__ import annotations

import os

from lattice.config import LatticeSettings, ProviderConfig

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def resolve_api_key(settings: LatticeSettings) -> str | None:
    return (
        settings.provider.api_key
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LATTICE_PROVIDER__API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
    )


def resolve_base_url(settings: LatticeSettings) -> str | None:
    return (
        settings.provider.base_url
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("LATTICE_PROVIDER__BASE_URL")
        or (OPENROUTER_BASE_URL if os.environ.get("OPENROUTER_API_KEY") else None)
    )


def resolve_model_id(
    settings: LatticeSettings,
    *,
    profile_model: str | None = None,
    sticky_model: str | None = None,
) -> str:
    """Active model id. Sticky chat override wins over profile and config."""
    if sticky_model:
        return sticky_model
    if profile_model:
        return profile_model
    return settings.agent.primary_model


def normalize_primary_model_id(raw: str) -> str:
    mid = (raw or "").strip()
    if not mid or "\n" in mid or "\r" in mid or len(mid) > 200:
        raise ValueError("invalid model id")
    return mid


def apply_provider_env(provider: ProviderConfig) -> None:
    if provider.api_key:
        os.environ.setdefault("OPENAI_API_KEY", provider.api_key)
    if provider.base_url:
        os.environ.setdefault("OPENAI_BASE_URL", provider.base_url)
