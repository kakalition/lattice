"""Action ledger: bounded, private records extracted from tool pairs."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from lattice.action_ledger import (
    ActionRecord,
    actions_from_messages,
    actions_from_tool_trace,
)
from lattice.prompt import build_action_notice
from lattice.turn_record import ToolRecord


def _pair(tool: str, args: dict, content: str) -> list:
    return [
        ModelResponse(parts=[ToolCallPart(tool_name=tool, args=args, tool_call_id="1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name=tool, content=content, tool_call_id="1")]),
    ]


def test_actions_pair_calls_with_returns() -> None:
    messages = _pair("read_file", {"path": "finance.py"}, "print('hi')")
    actions = actions_from_messages(messages)
    assert len(actions) == 1
    assert actions[0].tool == "read_file"
    assert actions[0].target == "finance.py"
    assert actions[0].ok is True


def test_error_and_denied_results_marked_not_ok() -> None:
    actions = actions_from_messages(_pair("shell", {"command": "rm x"}, "error: boom"))
    assert actions[0].ok is False
    denied = actions_from_messages(
        _pair("sqlite_execute", {"sql": "DELETE FROM t"}, "denied: deny")
    )
    assert denied[0].ok is False


def test_oversized_args_are_clipped() -> None:
    huge = "x" * 5000
    actions = actions_from_messages(_pair("read_file", {"path": huge}, "body"))
    assert len(actions[0].target) <= 121


def test_secret_looking_values_redacted() -> None:
    actions = actions_from_messages(
        _pair("shell", {"command": "curl -H api_key=supersecret"}, "ok")
    )
    assert "supersecret" not in actions[0].target


def test_artifacts_from_write_tools() -> None:
    actions = actions_from_messages(
        _pair("write_file", {"path": "out/chart.png", "content": "..."}, "wrote")
    )
    assert "out/chart.png" in actions[0].artifacts


def test_evidence_captured_for_reads_but_not_writes() -> None:
    read = actions_from_messages(_pair("read_file", {"path": "a.py"}, "print('hi')"))
    assert read[0].evidence
    assert "a.py" in read[0].evidence
    assert "sha=" in read[0].evidence
    assert "print('hi')" in read[0].evidence

    write = actions_from_messages(_pair("write_file", {"path": "a.py"}, "wrote"))
    assert write[0].evidence == ""


def test_evidence_notice_is_bounded_and_skips_empties() -> None:
    from lattice.prompt import build_evidence_notice

    assert build_evidence_notice([]) == ""
    assert build_evidence_notice([ActionRecord(tool="read_file", evidence="")]) == ""
    actions = [
        ActionRecord(tool="read_file", target=f"f{i}.py", evidence=f"f{i}.py sha=abc body{i}")
        for i in range(20)
    ]
    notice = build_evidence_notice(actions, limit=3, max_bytes=1000)
    assert len(notice.splitlines()) == 4  # header + 3 items
    assert "not repeat" in notice


def test_build_action_notice_renders_lines() -> None:
    notice = build_action_notice(
        [
            ActionRecord(tool="read_file", target="finance.py", ok=True),
            ActionRecord(tool="shell", target="ls", ok=False),
        ]
    )
    assert "read_file finance.py" in notice
    assert "failed" in notice
    assert build_action_notice([]) == ""


def test_synthesize_actions_from_tool_trace() -> None:
    tools = [
        ToolRecord(name="shell", duration_ms=1, ok=True, result_bytes=1, truncated=False),
        ToolRecord(name="write_file", duration_ms=1, ok=False, result_bytes=1, truncated=False),
    ]
    actions = actions_from_tool_trace(tools, media=[Path("out.png")])
    assert [a.tool for a in actions] == ["shell", "write_file"]
    assert actions[0].ok is True
    assert actions[1].ok is False
    assert "out.png" in actions[-1].artifacts


@pytest.mark.asyncio
async def test_tools_then_provider_error_persists_ledger(tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models import Model

    from lattice.config import LatticeSettings
    from lattice.memory import InMemoryMemory
    from lattice.models import Inbound
    from lattice.session import SessionStore
    from lattice.setup import init_home
    from lattice.turn import run_turn

    class _ToolThenErrorModel(Model):
        def __init__(self) -> None:
            self.calls = 0

        @property
        def model_name(self) -> str:
            return "tool-then-error"

        @property
        def system(self) -> str:
            return "fake"

        async def request(self, messages, model_settings, model_request_parameters):  # type: ignore[override]
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="calculator",
                            args={"expression": "2+3"},
                            tool_call_id="call-1",
                        )
                    ],
                    model_name="tool-then-error",
                )
            raise RuntimeError("provider blew up")

    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    store = SessionStore(tmp_path / "state.db")
    out = await run_turn(
        Inbound(text="calculate", profile_id="default", channel="cli", user_id="u"),
        settings=settings,
        session_store=store,
        model=_ToolThenErrorModel(),
        stream=False,
        memory=InMemoryMemory("t"),
    )
    saved = await store.get(out.session_id or "")
    assert saved is not None
    assert [a["tool"] for a in saved["actions"]] == ["calculator"]


@pytest.mark.asyncio
async def test_run_turn_persists_actions(tmp_path: Path) -> None:
    from pydantic_ai.models.test import TestModel

    from lattice.config import LatticeSettings
    from lattice.memory import InMemoryMemory
    from lattice.models import Inbound
    from lattice.session import SessionStore
    from lattice.setup import init_home
    from lattice.turn import run_turn

    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    store = SessionStore(tmp_path / "state.db")
    out = await run_turn(
        Inbound(text="calculate", profile_id="default", channel="cli", user_id="u"),
        settings=settings,
        session_store=store,
        model=TestModel(call_tools=["calculator"], custom_output_text="done"),
        memory=InMemoryMemory("t"),
    )
    saved = await store.get(out.session_id or "")
    assert saved is not None
    assert any(a["tool"] == "calculator" for a in saved["actions"])
