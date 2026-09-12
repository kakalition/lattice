"""Shared provider/model cache: reuse clients per connection inputs."""

from __future__ import annotations

from pathlib import Path

import pytest

from lattice.config import LatticeSettings
from lattice.providers.logging_model import LoggingModel, with_llm_logging
from lattice.providers.openai_compat import (
    clear_model_cache,
    get_cached_openai_model,
)
from lattice.session import SessionStore


@pytest.fixture(autouse=True)
def _clean_model_cache():
    clear_model_cache()
    yield
    clear_model_cache()


def _settings(tmp_path: Path) -> LatticeSettings:
    settings = LatticeSettings(home=tmp_path)
    settings.provider.base_url = "https://api.openai.com/v1"
    settings.provider.api_key = "test-key"
    return settings


def test_same_inputs_return_identical_model(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = get_cached_openai_model(settings, "openai:gpt-4o")
    second = get_cached_openai_model(settings, "openai:gpt-4o")
    assert first is second


def test_changed_model_id_rebuilds(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = get_cached_openai_model(settings, "openai:gpt-4o")
    second = get_cached_openai_model(settings, "openai:gpt-4o-mini")
    assert first is not second


def test_changed_key_rebuilds(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = get_cached_openai_model(settings, "openai:gpt-4o")
    settings.provider.api_key = "other-key"
    second = get_cached_openai_model(settings, "openai:gpt-4o")
    assert first is not second


def test_wrapping_is_idempotent_and_per_turn(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    shared = get_cached_openai_model(settings, "openai:gpt-4o")
    wrapped = with_llm_logging(shared)
    assert isinstance(wrapped, LoggingModel)
    assert wrapped.wrapped is shared
    assert with_llm_logging(wrapped) is wrapped
    # A new turn wraps the same shared client in a fresh counter wrapper.
    fresh = with_llm_logging(get_cached_openai_model(settings, "openai:gpt-4o"))
    assert fresh is not wrapped
    assert fresh.wrapped is shared


def test_clear_model_cache_rebuilds(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = get_cached_openai_model(settings, "openai:gpt-4o")
    clear_model_cache()
    second = get_cached_openai_model(settings, "openai:gpt-4o")
    assert first is not second


@pytest.mark.asyncio
async def test_model_change_evicts_cache(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = get_cached_openai_model(settings, "openai:gpt-4o")
    store = SessionStore(tmp_path / "state.db")
    await store.set_sticky_primary_model("cli", "local", "openai:gpt-4o-mini")
    second = get_cached_openai_model(settings, "openai:gpt-4o")
    assert first is not second
