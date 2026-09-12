"""Pressure calibration uses the real serialized request, not the stored transcript."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

from lattice.config import LatticeSettings
from lattice.context.pressure import (
    DEFAULT_MODEL_CONTEXT_TOKENS,
    PressureConfig,
    resolve_context_window,
)
from lattice.memory import InMemoryMemory
from lattice.models import Inbound
from lattice.providers.logging_model import with_llm_logging
from lattice.session import SessionStore
from lattice.setup import init_home
from lattice.turn import run_turn


class _FakeModel(Model):
    def __init__(self, response: ModelResponse) -> None:
        self._response = response

    @property
    def model_name(self) -> str:
        return "fake-model"

    @property
    def system(self) -> str:
        return "fake"

    async def request(self, messages, model_settings, model_request_parameters):  # type: ignore[override]
        return self._response


def _response() -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content="hello")],
        usage=RequestUsage(input_tokens=1000, output_tokens=10, cache_read_tokens=0),
        model_name="fake-model",
        finish_reason="stop",
    )


@pytest.mark.asyncio
async def test_logging_model_records_serialized_request_chars() -> None:
    messages = [ModelRequest(parts=[UserPromptPart(content="hello world")])]
    model = with_llm_logging(_FakeModel(_response()))
    await model.request(messages, None, ModelRequestParameters())
    expected = len(ModelMessagesTypeAdapter.dump_json(messages))
    assert model.last_request_chars == expected
    assert model.last_request_chars > 0
    assert model.last_input_tokens == 1000


def test_calibrated_estimate_reproduces_observed_tokens() -> None:
    pressure = PressureConfig(ratio=0.5, model_context_tokens=1000, chars_per_token=4.0)
    messages = [{"role": "user", "content": "x" * 400}]
    # 400 chars observed as 1200 tokens → the estimate must reproduce 1200.
    estimated = pressure.estimate_tokens(messages, observed_tokens=1200, observed_chars=400)
    assert estimated == pytest.approx(1200)


def test_resolve_context_window_config_wins_then_profile_then_default() -> None:
    class _Model:
        context_window = 32_000

    assert resolve_context_window(64_000, _Model()) == 64_000
    assert resolve_context_window(None, _Model()) == 32_000
    assert resolve_context_window(None, object()) == DEFAULT_MODEL_CONTEXT_TOKENS


@pytest.mark.asyncio
async def test_turn_records_real_request_chars_not_transcript_estimate(tmp_path: Path) -> None:
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    store = SessionStore(tmp_path / "state.db")
    out = await run_turn(
        Inbound(text="say hi", profile_id="default", channel="cli", user_id="u"),
        settings=settings,
        session_store=store,
        model=TestModel(custom_output_text="hi there"),
        memory=InMemoryMemory("t"),
    )
    saved = await store.get(out.session_id or "")
    assert saved is not None
    usage = saved["usage"]
    transcript_chars = PressureConfig().estimate_chars(saved["messages"])
    assert usage["last_context_chars"] > transcript_chars
    assert int(usage["last_input_tokens"]) >= 0
