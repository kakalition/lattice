"""OpenRouter model selection, prompt-cache settings, usage reports, MCP ordering."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.usage import RunUsage

from lattice.config import LatticeSettings
from lattice.providers.caching import prompt_cache_settings, supports_explicit_cache
from lattice.providers.openai_compat import build_openai_model, is_openrouter
from lattice.providers.usage import usage_to_dict


def _openrouter_settings(home: Path) -> LatticeSettings:
    settings = LatticeSettings(home=home)
    settings.provider.base_url = "https://openrouter.ai/api/v1"
    settings.provider.api_key = "test-key"
    return settings


def _openai_settings(home: Path) -> LatticeSettings:
    settings = LatticeSettings(home=home)
    settings.provider.base_url = "https://api.openai.com/v1"
    settings.provider.api_key = "test-key"
    return settings


def test_is_openrouter_detection(tmp_path: Path) -> None:
    assert is_openrouter(_openrouter_settings(tmp_path))
    assert not is_openrouter(_openai_settings(tmp_path))


def test_openrouter_base_url_builds_openrouter_model(tmp_path: Path) -> None:
    settings = _openrouter_settings(tmp_path)
    model = build_openai_model(settings, "anthropic/claude-3-5-sonnet")
    assert isinstance(model, OpenRouterModel)
    assert supports_explicit_cache(model)


def test_non_openrouter_keeps_openai_model(tmp_path: Path) -> None:
    settings = _openai_settings(tmp_path)
    model = build_openai_model(settings, "openai:gpt-4o")
    assert isinstance(model, OpenAIChatModel)
    assert not isinstance(model, OpenRouterModel)
    assert not supports_explicit_cache(model)
    assert prompt_cache_settings(settings, model) == {}


def test_prompt_cache_settings_for_anthropic(tmp_path: Path) -> None:
    settings = _openrouter_settings(tmp_path)
    model = build_openai_model(settings, "anthropic/claude-3-5-sonnet")
    cache = prompt_cache_settings(settings, model)
    assert cache.get("openrouter_cache_instructions") == "5m"
    assert cache.get("openrouter_cache_tool_definitions") == "5m"
    # One explicit boundary only; do not duplicate with tail caching.
    assert "openrouter_cache_messages" not in cache

    settings.agent.prompt_cache_ttl = "1h"
    assert prompt_cache_settings(settings, model).get("openrouter_cache_instructions") == "1h"


def test_openrouter_non_cache_providers_get_no_settings(tmp_path: Path) -> None:
    """DeepSeek / OpenAI OSS via OpenRouter use the adapter but send no markers."""
    settings = _openrouter_settings(tmp_path)
    for mid in ("deepseek/deepseek-v4.1-flash", "openai/gpt-oss-120b"):
        model = build_openai_model(settings, mid)
        assert isinstance(model, OpenRouterModel)
        assert not supports_explicit_cache(model)
        assert prompt_cache_settings(settings, model) == {}


def test_prompt_cache_disabled_is_noop(tmp_path: Path) -> None:
    settings = _openrouter_settings(tmp_path)
    settings.agent.prompt_cache = False
    model = build_openai_model(settings, "anthropic/claude-3-5-sonnet")
    assert prompt_cache_settings(settings, model) == {}


def test_usage_to_dict_reports_cache_metrics() -> None:
    usage = RunUsage(
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=80,
        cache_write_tokens=10,
        requests=2,
        cost=Decimal("0.02"),
    )
    data = usage_to_dict(usage, model="anthropic/claude-3-5-sonnet")
    assert data["model"] == "anthropic/claude-3-5-sonnet"
    assert data["cache_read_tokens"] == 80
    assert data["cache_write_tokens"] == 10
    assert data["cache_hit_ratio"] == 0.8
    assert data["requests"] == 2
    assert data["cost"] == 0.02
    assert usage_to_dict(None, model="m") == {"model": "m"}
