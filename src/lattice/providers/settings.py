"""Provider settings helpers."""

from __future__ import annotations

import os

from lattice.config import LatticeSettings, ProviderConfig


def resolve_api_key(settings: LatticeSettings) -> str | None:
    return (
        settings.provider.api_key
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LATTICE_PROVIDER__API_KEY")
    )


def model_name(settings: LatticeSettings, *, profile_model: str | None = None) -> str:
    return profile_model or settings.agent.model


def auxiliary_model_name(settings: LatticeSettings, *, profile_aux: str | None = None) -> str:
    return profile_aux or settings.agent.auxiliary_model


def apply_provider_env(provider: ProviderConfig) -> None:
    if provider.api_key:
        os.environ.setdefault("OPENAI_API_KEY", provider.api_key)
    if provider.base_url:
        os.environ.setdefault("OPENAI_BASE_URL", provider.base_url)
