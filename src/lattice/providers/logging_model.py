"""Per-call LLM logging: wall time and token breakdown for every model request.

Provider SDKs log the HTTP round trip but nothing about cost or cache behaviour.
Wrapping the model gives one log line per request — including each step of the
tool loop — carrying prompt tokens, cache reads/writes, completion tokens, and
reasoning ("thinking") tokens. Correlated to a turn via ``current_turn_id``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Any

from pydantic_ai._run_context import RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from lattice.eval.cassette import get_recorder
from lattice.turn_trace import current_turn_id

logger = logging.getLogger("lattice.llm")


def _thinking_tokens(usage: RequestUsage | None) -> int:
    """Reasoning tokens: OpenAI-compatible adapters flatten them into ``details``."""
    if usage is None:
        return 0
    for key in ("reasoning_tokens", "thinking_tokens"):
        value = usage.details.get(key)
        if isinstance(value, int):
            return value
    return 0


def _format_call(
    *,
    call: int,
    model: str | None,
    duration_ms: int,
    usage: RequestUsage | None,
    stream: bool,
    finish_reason: str | None,
    error: BaseException | None,
) -> str:
    turn = current_turn_id() or "-"
    if error is not None:
        return (
            f"turn={turn} llm_call={call} model={model or '-'} stream={int(stream)} "
            f"duration_ms={duration_ms} error={type(error).__name__}: {error}"
        )
    prompt = usage.input_tokens if usage else 0
    cached = usage.cache_read_tokens if usage else 0
    cache_write = usage.cache_write_tokens if usage else 0
    output = usage.output_tokens if usage else 0
    thinking = _thinking_tokens(usage)
    cost = f"{float(usage.cost):.6f}" if usage and usage.cost is not None else "-"
    return (
        f"turn={turn} llm_call={call} model={model or '-'} stream={int(stream)} "
        f"duration_ms={duration_ms} "
        f"prompt={prompt} cached={cached} new={max(prompt - cached, 0)} "
        f"cache_write={cache_write} output={output} thinking={thinking} "
        f"total={prompt + output} cost={cost} finish={finish_reason or '-'}"
    )


class LoggingModel(WrapperModel):
    """Wrap a model to log duration and token usage for every request."""

    # ``Model`` declares these as ClassVars and reads them via ``self``, so a plain
    # wrapper would inherit the empty defaults and silently lose capabilities such
    # as tool deferral. Copy them onto the instance from the wrapped adapter.
    _FORWARDED_CAPABILITIES = (
        "supported_tool_deferral_modes",
        "supported_tool_addition_modes",
        "compaction_requires_encrypted_content",
        "compaction_retains_standing_prompt",
    )

    def __init__(self, wrapped: Model) -> None:
        super().__init__(wrapped)
        for attr in self._FORWARDED_CAPABILITIES:
            setattr(self, attr, getattr(type(self.wrapped), attr))
        self._calls = 0
        self._model_name: str | None = None
        # Input tokens of the most recent successful request: the closest thing
        # to the live context size, used to calibrate compression pressure.
        self.last_input_tokens = 0

    def _name(self) -> str:
        # Cache the name: ``model_name`` is cheap, but ``__getattr__`` forwarding
        # makes a failed lookup expensive during error paths.
        if self._model_name is None:
            try:
                self._model_name = self.wrapped.model_name
            except Exception:
                self._model_name = "unknown"
        return self._model_name

    def _log(
        self,
        *,
        call: int,
        duration_s: float,
        usage: RequestUsage | None,
        stream: bool,
        finish_reason: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        logger.info(
            _format_call(
                call=call,
                model=self._name(),
                duration_ms=int(duration_s * 1000),
                usage=usage,
                stream=stream,
                finish_reason=finish_reason,
                error=error,
            )
        )

    def _record(
        self,
        messages: list[ModelMessage],
        response: ModelResponse | None,
        usage: RequestUsage | None,
        *,
        stream: bool,
    ) -> None:
        recorder = get_recorder()
        if recorder is not None:
            recorder.record(messages=messages, response=response, usage=usage, stream=stream)

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        call = self._calls + 1
        self._calls = call
        started = time.perf_counter()
        try:
            response = await self.wrapped.request(
                messages, model_settings, model_request_parameters
            )
        except Exception as exc:
            self._log(
                call=call,
                duration_s=time.perf_counter() - started,
                usage=None,
                stream=False,
                error=exc,
            )
            raise
        self._log(
            call=call,
            duration_s=time.perf_counter() - started,
            usage=response.usage,
            stream=False,
            finish_reason=response.finish_reason,
        )
        self._record(messages, response, response.usage, stream=False)
        if response.usage is not None:
            self.last_input_tokens = response.usage.input_tokens
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        call = self._calls + 1
        self._calls = call
        started = time.perf_counter()
        try:
            async with self.wrapped.request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as response_stream:
                yield response_stream
        except Exception as exc:
            self._log(
                call=call,
                duration_s=time.perf_counter() - started,
                usage=None,
                stream=True,
                error=exc,
            )
            raise
        self._log(
            call=call,
            duration_s=time.perf_counter() - started,
            usage=getattr(response_stream, "usage", None),
            stream=True,
        )
        final: ModelResponse | None = None
        with suppress(Exception):
            final = response_stream.get()
        stream_usage = getattr(response_stream, "usage", None)
        self._record(messages, final, stream_usage, stream=True)
        if stream_usage is not None:
            self.last_input_tokens = stream_usage.input_tokens


def with_llm_logging(model: Model) -> Model:
    """Idempotently wrap ``model`` so every request emits a usage log line."""
    if isinstance(model, LoggingModel):
        return model
    return LoggingModel(model)
