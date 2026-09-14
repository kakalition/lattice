"""Invalid destructive ops must error before HITL, not prompt for doomed work."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from lattice.agent_app import build_core_toolset
from lattice.config import LatticeSettings
from lattice.deps import TurnDeps
from lattice.events import NullTurnEvents
from lattice.hitl import ApprovalDecision, ApprovalRequest
from lattice.mcp import McpHostManager
from lattice.memory import InMemoryMemory
from lattice.profiles import Profile, ensure_default_profile
from lattice.session import SessionStore
from lattice.sqlite import SqlitePool, SqliteRegistry
from lattice.tools.files import validate_removal


@dataclass
class RecordingHitl:
    decision: ApprovalDecision = ApprovalDecision.DENY
    calls: list[str] = field(default_factory=list)

    async def approve(self, req: ApprovalRequest) -> ApprovalDecision:
        self.calls.append(req.tool_name)
        return self.decision

    async def clarify(self, req: Any) -> str:
        return "ok"


def _deps(tmp_path: Path, hitl: RecordingHitl, enabled: list[str]) -> TurnDeps:
    ensure_default_profile(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    registry = SqliteRegistry(settings)
    return TurnDeps(
        settings=settings,
        profile=Profile(id="default"),
        hitl=hitl,
        session=SessionStore(tmp_path / "state.db"),
        session_id="s",
        memory=InMemoryMemory("t"),
        sqlite_registry=registry,
        sqlite_pool=SqlitePool(registry),
        mcp=McpHostManager(),
        events=NullTurnEvents(),
        workspace=tmp_path,
        enabled_tools=enabled,
    )


def _invoke_tool(deps: TurnDeps, name: str, args: dict[str, Any]) -> list[ToolReturnPart]:
    async def fn(messages, info):
        for message in messages:
            for part in getattr(message, "parts", []):
                if isinstance(part, ToolReturnPart):
                    return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(name, args)])

    settings = deps.settings
    agent = Agent(
        FunctionModel(fn),
        deps_type=TurnDeps,
        system_prompt="t",
        toolsets=[build_core_toolset(settings, McpHostManager())],
    )
    result = asyncio.run(agent.run("go", deps=deps))
    return [
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    ]


def test_validate_removal_rejects_out_of_jail(tmp_path: Path) -> None:
    outside = str(tmp_path.parent / "escape.txt")
    error = validate_removal(outside, workspace=tmp_path, home=tmp_path)
    assert error is not None and "outside" in error


def test_validate_removal_rejects_nonempty_dir_without_recursive(tmp_path: Path) -> None:
    target = tmp_path / "d"
    target.mkdir()
    (target / "f.txt").write_text("x")
    error = validate_removal("d", workspace=tmp_path, home=tmp_path)
    assert error is not None and "not empty" in error


def test_out_of_jail_remove_path_never_prompts(tmp_path: Path) -> None:
    hitl = RecordingHitl(ApprovalDecision.APPROVE)
    deps = _deps(tmp_path, hitl, ["files/remove"])
    outside = str(tmp_path.parent / "escape.txt")

    returns = _invoke_tool(deps, "files__remove", {"path": outside})

    assert hitl.calls == [], "out-of-jail removal must not trigger HITL"
    assert returns and "outside" in str(returns[0].content)


def test_in_jail_remove_path_still_prompts(tmp_path: Path) -> None:
    hitl = RecordingHitl(ApprovalDecision.DENY)
    deps = _deps(tmp_path, hitl, ["files/remove"])
    (tmp_path / "a.txt").write_text("x")

    returns = _invoke_tool(deps, "files__remove", {"path": "a.txt"})

    assert hitl.calls == ["files/remove"]
    assert returns and "denied" in str(returns[0].content)


def test_invalid_profile_remove_never_prompts(tmp_path: Path) -> None:
    ensure_default_profile(tmp_path)
    hitl = RecordingHitl(ApprovalDecision.APPROVE)
    deps = _deps(tmp_path, hitl, ["profiles/remove"])
    # profile_remove is cold/deferred by default; force it eager so the model can
    # call it directly and we exercise the tool body.
    deps.settings.tools.eager = ["profiles/remove"]

    returns = _invoke_tool(deps, "profiles__remove", {"profile_id": "default"})

    assert hitl.calls == []
    assert returns and "default" in str(returns[0].content)


@pytest.mark.parametrize("profile_id", ["../evil", "bad id"])
def test_malformed_profile_id_errors(tmp_path: Path, profile_id: str) -> None:
    from lattice.profiles import validate_removable_profile

    error = validate_removable_profile(profile_id, tmp_path)
    assert error is not None and error.startswith("error:")
