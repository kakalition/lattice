"""Per-LLM-call logging: duration and token breakdown."""

from __future__ import annotations

import logging

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.usage import RequestUsage

from lattice.providers.logging_model import with_llm_logging
from lattice.turn_trace import LoggingTurnEvents


class _FakeModel(Model):
    def __init__(
        self, response: ModelResponse | None = None, error: BaseException | None = None
    ) -> None:
        self._response = response
        self._error = error

    @property
    def model_name(self) -> str:
        return "fake-model"

    @property
    def system(self) -> str:
        return "fake"

    async def request(self, messages, model_settings, model_request_parameters):  # type: ignore[override]
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _response() -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content="hello")],
        usage=RequestUsage(
            input_tokens=1000,
            cache_read_tokens=900,
            cache_write_tokens=5,
            output_tokens=40,
            details={"reasoning_tokens": 12},
        ),
        model_name="fake-model",
        finish_reason="stop",
    )


@pytest.mark.asyncio
async def test_logging_model_reports_tokens_and_duration(caplog) -> None:
    model = with_llm_logging(_FakeModel(response=_response()))
    with caplog.at_level(logging.INFO, logger="lattice.llm"):
        out = await model.request([], None, ModelRequestParameters())
    assert out.parts[0].content == "hello"
    text = "\n".join(r.message for r in caplog.records)
    assert "llm_call=1" in text
    assert "model=fake-model" in text
    assert "duration_ms=" in text
    assert "prompt=1000" in text
    assert "cached=900" in text
    assert "new=100" in text
    assert "cache_write=5" in text
    assert "output=40" in text
    assert "thinking=12" in text
    assert "total=1040" in text
    assert "finish=stop" in text


@pytest.mark.asyncio
async def test_logging_model_increments_call_and_correlates_turn(caplog) -> None:
    trace = LoggingTurnEvents("turn42")
    trace.log_begin(
        channel="cli",
        user_id="1",
        profile_id="default",
        session_id="s1",
        inbound_text="hi",
        tools=[],
        skills=[],
    )
    model = with_llm_logging(_FakeModel(response=_response()))
    with caplog.at_level(logging.INFO, logger="lattice.llm"):
        await model.request([], None, ModelRequestParameters())
        await model.request([], None, ModelRequestParameters())
    text = "\n".join(r.message for r in caplog.records)
    assert "turn=turn42 llm_call=1" in text
    assert "turn=turn42 llm_call=2" in text


@pytest.mark.asyncio
async def test_logging_model_logs_errors(caplog) -> None:
    model = with_llm_logging(_FakeModel(error=RuntimeError("boom")))
    with caplog.at_level(logging.INFO, logger="lattice.llm"), pytest.raises(RuntimeError):
        await model.request([], None, ModelRequestParameters())
    text = "\n".join(r.message for r in caplog.records)
    assert "duration_ms=" in text
    assert "error=RuntimeError: boom" in text


def test_with_llm_logging_is_idempotent() -> None:
    model = with_llm_logging(_FakeModel(response=_response()))
    assert with_llm_logging(model) is model


class _DeferringModel(_FakeModel):
    supported_tool_deferral_modes = frozenset({"with_tool_search"})


def test_logging_model_forwards_model_capabilities() -> None:
    """Wrapping must not reset ClassVar capabilities to the empty Model default."""
    model = with_llm_logging(_DeferringModel(response=_response()))
    assert model.supported_tool_deferral_modes == frozenset({"with_tool_search"})


def test_logging_wrapper_preserves_real_model_capabilities(tmp_path) -> None:
    from lattice.config import LatticeSettings
    from lattice.providers.openai_compat import build_openai_model

    settings = LatticeSettings(home=tmp_path)
    settings.provider.base_url = "https://api.openai.com/v1"
    settings.provider.api_key = "test-key"
    inner = build_openai_model(settings, "openai:gpt-4o")
    model = with_llm_logging(inner)
    assert model.supported_tool_deferral_modes == inner.supported_tool_deferral_modes
    assert model.supported_tool_addition_modes == inner.supported_tool_addition_modes
    assert (
        model.compaction_requires_encrypted_content == inner.compaction_requires_encrypted_content
    )
