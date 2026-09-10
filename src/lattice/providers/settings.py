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


def resolve_model_id(settings: LatticeSettings, *, profile_model: str | None = None) -> str:
    if profile_model:
        return profile_model
    return (
        os.environ.get("OPENROUTER_MODEL")
        or os.environ.get("LATTICE_AGENT__MODEL")
        or settings.agent.model
    )


def model_name(settings: LatticeSettings, *, profile_model: str | None = None) -> str:
    return resolve_model_id(settings, profile_model=profile_model)


def auxiliary_model_name(settings: LatticeSettings, *, profile_aux: str | None = None) -> str:
    return profile_aux or settings.agent.auxiliary_model


def apply_provider_env(provider: ProviderConfig) -> None:
    if provider.api_key:
        os.environ.setdefault("OPENAI_API_KEY", provider.api_key)
    if provider.base_url:
        os.environ.setdefault("OPENAI_BASE_URL", provider.base_url)
