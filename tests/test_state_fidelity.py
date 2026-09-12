"""Task-state fidelity and tool-economy fixes (Tracks A and B).

Forced compression uses a failing summarizer stub so ``compress`` takes its
deterministic trim fallback and no API key is needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models import Model

import lattice.turn as turn_mod
from lattice.action_ledger import ActionRecord, actions_from_tool_trace
from lattice.config import LatticeSettings
from lattice.memory import InMemoryMemory
from lattice.models import Inbound
from lattice.session import SessionStore
from lattice.setup import init_home
from lattice.tools.agent import default_eager_names
from lattice.turn import run_turn
from lattice.turn_record import ToolRecord
from lattice.turn_trace import LoggingTurnEvents


class _FailingSummarizer:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def summarize(self, _transcript: str, *, max_words: int = 400) -> str:
        raise RuntimeError("summarizer unavailable in tests")


class _ScriptedModel(Model):
    """Returns pre-scripted responses (or raises) one model call at a time."""

    def __init__(self, steps: list[Any]) -> None:
        self.steps = list(steps)
        self.calls = 0
        self.seen: list[Any] = []

    @property
    def model_name(self) -> str:
        return "scripted"

    @property
    def system(self) -> str:
        return "fake"

    async def request(self, messages, model_settings, model_request_parameters):  # type: ignore[override]
        self.calls += 1
        self.seen.append(ModelMessagesTypeAdapter.dump_python(messages, mode="json"))
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)], model_name="scripted")


def _call(name: str, args: dict[str, Any], call_id: str) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=call_id)],
        model_name="scripted",
    )


def _settings(tmp_path: Path) -> LatticeSettings:
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    return settings


def _history(n: int, *, size: int = 40) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"m{i} " + "x" * size})
    return out


@pytest.mark.asyncio
async def test_compression_persists_summary_and_carries_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(turn_mod, "Summarizer", _FailingSummarizer)
    settings = _settings(tmp_path)
    settings.agent.protect_last_n = 3
    settings.agent.context_window_tokens = 200
    store = SessionStore(tmp_path / "state.db")
    parent = await store.create(profile_id="default", user_id="u", channel="cli")
    await store.save_messages(parent, _history(10, size=60))
    await store.append_actions(parent, [ActionRecord(tool="read_file", target="prior.py", ok=True)])

    out = await run_turn(
        Inbound(
            text="continue", profile_id="default", channel="cli", user_id="u", session_id=parent
        ),
        settings=settings,
        session_store=store,
        model=_ScriptedModel([_text("done")]),
        stream=False,
        memory=InMemoryMemory("t"),
    )

    assert out.session_id != parent
    child = await store.get(out.session_id or "")
    assert child is not None
    # The compressed transcript is persisted, not lost.
    assert any(m.get("role") == "summary" for m in child["messages"])
    assert any("[compressed context summary]" in str(m.get("content")) for m in child["messages"])
    # The prior ledger is carried into the child.
    assert any(a["tool"] == "read_file" and a["target"] == "prior.py" for a in child["actions"])


@pytest.mark.asyncio
async def test_compression_gate_uses_pending_user_chars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B3: pressure driven only by the pending user text must still compress."""
    monkeypatch.setattr(turn_mod, "Summarizer", _FailingSummarizer)
    settings = _settings(tmp_path)
    settings.agent.protect_last_n = 3
    settings.agent.context_window_tokens = 100
    store = SessionStore(tmp_path / "state.db")
    parent = await store.create(profile_id="default", user_id="u", channel="cli")
    # Stored transcript is short; only the new user text pushes it over.
    await store.save_messages(parent, _history(6, size=1))

    out = await run_turn(
        Inbound(
            text="y" * 4000,
            profile_id="default",
            channel="cli",
            user_id="u",
            session_id=parent,
        ),
        settings=settings,
        session_store=store,
        model=_ScriptedModel([_text("done")]),
        stream=False,
        memory=InMemoryMemory("t"),
    )

    child = await store.get(out.session_id or "")
    assert child is not None
    assert any(m.get("role") == "summary" for m in child["messages"])


@pytest.mark.asyncio
async def test_todo_round_trip_across_turns(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SessionStore(tmp_path / "state.db")

    first = await run_turn(
        Inbound(text="plan", profile_id="default", channel="cli", user_id="u"),
        settings=settings,
        session_store=store,
        model=_ScriptedModel(
            [
                _call("todo", {"action": "add", "text": "alpha"}, "t1"),
                _text("ok"),
            ]
        ),
        stream=False,
        memory=InMemoryMemory("t"),
    )
    saved = await store.get(first.session_id or "")
    assert saved is not None
    assert saved["todos"] == [{"id": 1, "text": "alpha", "done": False}]

    # Second turn loads the list and its ``_next_id``, so the new item gets id 2.
    second = await run_turn(
        Inbound(
            text="more",
            profile_id="default",
            channel="cli",
            user_id="u",
            session_id=first.session_id,
        ),
        settings=settings,
        session_store=store,
        model=_ScriptedModel(
            [
                _call("todo", {"action": "add", "text": "beta"}, "t2"),
                _text("ok"),
            ]
        ),
        stream=False,
        memory=InMemoryMemory("t"),
    )
    saved2 = await store.get(second.session_id or "")
    assert saved2 is not None
    assert [t["id"] for t in saved2["todos"]] == [1, 2]
    assert [t["text"] for t in saved2["todos"]] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_todo_survives_compression(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(turn_mod, "Summarizer", _FailingSummarizer)
    settings = _settings(tmp_path)
    settings.agent.protect_last_n = 3
    settings.agent.context_window_tokens = 200
    store = SessionStore(tmp_path / "state.db")
    parent = await store.create(profile_id="default", user_id="u", channel="cli")
    await store.save_messages(parent, _history(10, size=60))
    from lattice.tools.todo import TodoList

    await store.save_todos(
        parent, TodoList.from_items([{"id": 1, "text": "keep", "done": False}]).items
    )

    out = await run_turn(
        Inbound(
            text="continue", profile_id="default", channel="cli", user_id="u", session_id=parent
        ),
        settings=settings,
        session_store=store,
        model=_ScriptedModel([_text("done")]),
        stream=False,
        memory=InMemoryMemory("t"),
    )
    child = await store.get(out.session_id or "")
    assert child is not None
    assert [t["text"] for t in child["todos"]] == ["keep"]


@pytest.mark.asyncio
async def test_abort_trace_preserves_tool_operands() -> None:
    trace = LoggingTurnEvents("trace1")
    await trace.on_tool_start("write_file", {"path": "out.txt", "content": "x"})
    await trace.on_tool_end("write_file", "wrote out.txt")
    assert trace.tools[0].args.get("path") == "out.txt"

    records = actions_from_tool_trace(trace.tools)
    assert records[0].tool == "write_file"
    assert records[0].target == "out.txt"
    assert "out.txt" in records[0].artifacts


def test_actions_from_tool_trace_keeps_legacy_shape() -> None:
    # ToolRecords persisted/constructed without args must still produce records.
    tools = [ToolRecord(name="shell", duration_ms=1, ok=True, result_bytes=1, truncated=False)]
    records = actions_from_tool_trace(tools)
    assert records[0].tool == "shell"
    assert records[0].target == ""


@pytest.mark.asyncio
async def test_context_overflow_after_tools_does_not_replay(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SessionStore(tmp_path / "state.db")
    model = _ScriptedModel(
        [
            _call("calculator", {"expression": "2+3"}, "c1"),
            RuntimeError("maximum context length exceeded"),
        ]
    )
    out = await run_turn(
        Inbound(text="calculate", profile_id="default", channel="cli", user_id="u"),
        settings=settings,
        session_store=store,
        model=model,
        stream=False,
        memory=InMemoryMemory("t"),
    )
    # One tool round + the failing request; the turn must not re-run the tool.
    assert model.calls == 2
    assert "context-overflow" in out.text


@pytest.mark.asyncio
async def test_replay_evidence_gate_default_on_and_opt_out(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert settings.agent.replay_evidence is True
    store = SessionStore(tmp_path / "state.db")
    sid = await store.create(profile_id="default", user_id="u", channel="cli")
    await store.append_actions(
        sid,
        [ActionRecord(tool="read_file", target="a.py", evidence="a.py sha=abc print('hi')")],
    )

    on = _ScriptedModel([_text("done")])
    await run_turn(
        Inbound(text="again", profile_id="default", channel="cli", user_id="u", session_id=sid),
        settings=settings,
        session_store=store,
        model=on,
        stream=False,
        memory=InMemoryMemory("t"),
    )
    assert "Recent evidence" in str(on.seen)

    settings.agent.replay_evidence = False
    off = _ScriptedModel([_text("done")])
    await run_turn(
        Inbound(text="again", profile_id="default", channel="cli", user_id="u", session_id=sid),
        settings=settings,
        session_store=store,
        model=off,
        stream=False,
        memory=InMemoryMemory("t"),
    )
    assert "Recent evidence" not in str(off.seen)


def test_eager_set_includes_harness_named_tools() -> None:
    eager = set(default_eager_names())
    for name in ("schedule_add", "skills_list", "skill_view", "sqlite_schema", "ocr"):
        assert name in eager
    assert len(default_eager_names()) == len(eager)
