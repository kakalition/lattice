"""OpenAI-compat model factory via Pydantic AI."""

from __future__ import annotations

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from lattice.config import LatticeSettings
from lattice.providers.settings import (
    apply_provider_env,
    resolve_api_key,
    resolve_base_url,
    resolve_model_id,
)


def build_openai_model(settings: LatticeSettings, model_id: str | None = None) -> OpenAIChatModel:
    apply_provider_env(settings.provider)
    api_key = resolve_api_key(settings) or "sk-unset"
    name = model_id or resolve_model_id(settings)
    # Strip provider prefix if present (openai:gpt-4o → gpt-4o)
    if ":" in name:
        _, name = name.split(":", 1)
    provider_kwargs: dict = {"api_key": api_key}
    base_url = resolve_base_url(settings)
    if base_url:
        provider_kwargs["base_url"] = base_url
    provider = OpenAIProvider(**provider_kwargs)
    return OpenAIChatModel(name, provider=provider)
