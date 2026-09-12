"""LLM cassette record/replay.

Recording is interception-based: ``LoggingModel`` already sees every
request/response, so it only needs a sink. Enabling recording is opt-in via
``LATTICE_CASSETTE_DIR``; replay needs no credentials or network.

The request digest is computed over a *stabilized* rendering of the messages:
volatile per-turn ``[notice]`` lines (runtime clock, ledger, memories) are
dropped so prompt drift reflects real change, not the current time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.models import CompletedStreamedResponse, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from lattice.turn_trace import current_turn_id

logger = logging.getLogger("lattice.eval.cassette")

_NOTICE_RE = re.compile(r"^\s*\[notice\].*$", re.MULTILINE)
# Environment-dependent lines live in the (cacheable) system prefix now, but they
# still differ between record and replay hosts: normalize them out of the digest
# so a committed cassette is portable and drift reflects real prompt changes.
_ENV_LINE_RE = re.compile(
    r"^(?:Runtime: workspace=|Registered DBs:|Canonical .*DB:).*$", re.MULTILINE
)
# Per-message metadata that changes between record and replay.
_VOLATILE_KEYS = frozenset({"timestamp", "run_id", "conversation_id"})

cassette_sink: ContextVar[CassetteRecorder | None] = ContextVar(
    "lattice_cassette_sink", default=None
)


class CassetteEntry(BaseModel):
    call: int
    request_digest: str
    request_json: list[Any] = Field(default_factory=list)
    response_json: Any | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    stream: bool = False


def _scrub_text(text: str) -> str:
    """Drop volatile/environment lines from a prompt string before digesting."""
    text = _ENV_LINE_RE.sub("", text)
    if not text.lstrip().startswith("[notice]"):
        return _NOTICE_RE.sub("", text)
    # The preamble is a run of notice lines separated from the user text by a
    # blank line; keep only what follows it.
    idx = text.find("\n\n")
    return text[idx + 2 :] if idx != -1 else ""


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def request_digest(messages: list[ModelMessage]) -> str:
    payload = _scrub(ModelMessagesTypeAdapter.dump_python(messages, mode="json"))
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class CassetteRecorder:
    def __init__(self, directory: Path, turn_id: str) -> None:
        self.directory = directory
        self.turn_id = turn_id
        self.path = directory / f"{turn_id}.jsonl"
        self.calls = 0

    def record(
        self,
        *,
        messages: list[ModelMessage],
        response: ModelResponse | None,
        usage: Any | None,
        stream: bool,
    ) -> None:
        self.calls += 1
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            entry = CassetteEntry(
                call=self.calls,
                request_digest=request_digest(messages),
                request_json=ModelMessagesTypeAdapter.dump_python(messages, mode="json"),
                response_json=(
                    ModelMessagesTypeAdapter.dump_python([response], mode="json")[0]
                    if response is not None
                    else None
                ),
                usage=_usage_dict(usage),
                stream=stream,
            )
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(entry.model_dump_json() + "\n")
        except Exception:
            logger.warning("cassette record failed", exc_info=True)


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    return {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_read_tokens": getattr(usage, "cache_read_tokens", 0),
        "cache_write_tokens": getattr(usage, "cache_write_tokens", 0),
    }


def get_recorder() -> CassetteRecorder | None:
    """Active recorder: explicit ContextVar sink, else env-configured directory."""
    sink = cassette_sink.get()
    if sink is not None:
        return sink
    directory = os.environ.get("LATTICE_CASSETTE_DIR")
    if not directory:
        return None
    turn_id = current_turn_id() or "unknown"
    recorder = _env_recorders.get((directory, turn_id))
    if recorder is None:
        recorder = CassetteRecorder(Path(directory), turn_id)
        _env_recorders[(directory, turn_id)] = recorder
    return recorder


_env_recorders: dict[tuple[str, str], CassetteRecorder] = {}


def load_cassette(path: Path) -> list[CassetteEntry]:
    entries: list[CassetteEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(CassetteEntry.model_validate_json(line))
    return entries


class ReplayModel(WrapperModel):
    """Serve recorded ``ModelResponse``s; never touches the network."""

    def __init__(self, entries: list[CassetteEntry]) -> None:
        super().__init__(TestModel())
        self._entries = entries
        self._used: set[int] = set()
        self.prompt_drift = False
        self.matched_digest = False

    def _next_entry(self, messages: list[ModelMessage]) -> CassetteEntry:
        digest = request_digest(messages)
        for index, entry in enumerate(self._entries):
            if index not in self._used and entry.request_digest == digest:
                self._used.add(index)
                self.matched_digest = True
                return entry
        for index, entry in enumerate(self._entries):
            if index not in self._used:
                self._used.add(index)
                self.prompt_drift = True
                return entry
        raise RuntimeError("replay cassette exhausted")

    def _response(self, entry: CassetteEntry) -> ModelResponse:
        if entry.response_json is None:
            raise RuntimeError("cassette entry has no recorded response")
        return ModelMessagesTypeAdapter.validate_python([entry.response_json])[0]

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        return self._response(self._next_entry(messages))

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        response = self._response(self._next_entry(messages))
        yield CompletedStreamedResponse(
            response,
            model_request_parameters=model_request_parameters,
            replay_events=True,
        )


def build_replay_model(cassette_path: Path) -> ReplayModel:
    return ReplayModel(load_cassette(cassette_path))
