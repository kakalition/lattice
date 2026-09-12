"""OpenAI-compat model factory via Pydantic AI.

OpenRouter is detected by base URL and gets the dedicated ``OpenRouterModel`` +
``OpenRouterProvider`` pair, which is what exposes explicit prompt-cache control
(``CachePoint`` and the ``openrouter_*`` settings). Every other endpoint keeps the
plain ``OpenAIChatModel`` path, where providers auto-cache and no explicit
breakpoints are sent.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from lattice.config import LatticeSettings
from lattice.providers.settings import (
    apply_provider_env,
    resolve_api_key,
    resolve_base_url,
    resolve_model_id,
)

_OPENROUTER_HOST = "openrouter.ai"

# Model/provider construction owns an ``AsyncOpenAI`` client (connection pool +
# TLS). Rebuilding it every turn is pure latency; cache the bare model keyed by
# every connection input and wrap per-turn so usage counters stay turn-local.
_MODEL_CACHE_MAX = 8
_model_cache: OrderedDict[tuple[Any, ...], Model] = OrderedDict()
_model_lock = threading.Lock()


def is_openrouter(settings: LatticeSettings) -> bool:
    """True when the resolved base URL points at OpenRouter."""
    base_url = resolve_base_url(settings) or ""
    return _OPENROUTER_HOST in base_url


def build_openai_model(settings: LatticeSettings, model_id: str | None = None) -> Model:
    apply_provider_env(settings.provider)
    api_key = resolve_api_key(settings) or "sk-unset"
    name = model_id or resolve_model_id(settings)
    if is_openrouter(settings) and "/" in name:
        # OpenRouter ids are ``provider/model`` and may carry a ``:tag``; the
        # provider needs the full id intact to select the downstream profile.
        # Ids without a ``/`` are not valid OpenRouter names, so they keep the
        # legacy OpenAI-compatible path (and its ``provider:model`` stripping).
        return OpenRouterModel(name, provider=OpenRouterProvider(api_key=api_key))
    # Strip provider prefix if present (openai:gpt-4o → gpt-4o)
    if ":" in name:
        _, name = name.split(":", 1)
    provider_kwargs: dict = {"api_key": api_key}
    base_url = resolve_base_url(settings)
    if base_url:
        provider_kwargs["base_url"] = base_url
    provider = OpenAIProvider(**provider_kwargs)
    return OpenAIChatModel(name, provider=provider)


def _cache_key(settings: LatticeSettings, model_id: str) -> tuple[Any, ...]:
    apply_provider_env(settings.provider)
    return (
        resolve_base_url(settings) or "",
        resolve_api_key(settings) or "",
        model_id,
        is_openrouter(settings),
    )


def get_cached_openai_model(settings: LatticeSettings, model_id: str | None = None) -> Model:
    """Return a shared (unwrapped) model for the current connection inputs.

    Distinct turns with the same base URL/key/model then reuse one provider
    client; changing any input (e.g. ``/model``) builds a fresh one. Callers wrap
    with ``with_llm_logging`` per turn so counters are not shared.
    """
    name = model_id or resolve_model_id(settings)
    key = _cache_key(settings, name)
    with _model_lock:
        cached = _model_cache.get(key)
        if cached is not None:
            _model_cache.move_to_end(key)
            return cached
    model = build_openai_model(settings, name)
    with _model_lock:
        _model_cache[key] = model
        _model_cache.move_to_end(key)
        while len(_model_cache) > _MODEL_CACHE_MAX:
            _model_cache.popitem(last=False)
    return model


def clear_model_cache() -> None:
    with _model_lock:
        _model_cache.clear()
