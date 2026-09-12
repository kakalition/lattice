"""Action ledger: bounded, private records extracted from tool pairs."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from lattice.action_ledger import ActionRecord, actions_from_messages
from lattice.prompt import build_action_notice


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
