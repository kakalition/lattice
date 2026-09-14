"""User-authored script-backed tools: manifests, adapter, wiring."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lattice.agent_app import (
    _toolset_cache_key,
    build_core_toolset,
    resolve_enabled_tools,
    resolve_tool_policy,
)
from lattice.config import LatticeSettings, ToolsConfig
from lattice.deps import TurnDeps
from lattice.events import NullTurnEvents
from lattice.hitl import ApprovalDecision, ApprovalRequest
from lattice.mcp import McpHostManager
from lattice.memory import InMemoryMemory
from lattice.profiles.load import Profile
from lattice.session import SessionStore
from lattice.sqlite import SqlitePool, SqliteRegistry
from lattice.tools.middleware import GuardedToolset
from lattice.tools.user_tools import (
    ToolHandler,
    UserToolset,
    UserToolSpec,
    scan_user_tools,
    validate_args,
)


def _guarded(toolset: UserToolset) -> GuardedToolset:
    """Wrap a bare user toolset with the same middleware the agent uses."""
    return GuardedToolset(wrapped=toolset, resolve=resolve_tool_policy)


BENIGN = (
    "import json, os, sys\n"
    "args = json.load(sys.stdin)\n"
    "print('greet', args['name'])\n"
    "print('tool', os.environ.get('LATTICE_TOOL_NAME'))\n"
    "print('args', os.environ.get('LATTICE_TOOL_ARGS'))\n"
)


@dataclass
class RecordingHitl:
    decision: ApprovalDecision = ApprovalDecision.DENY
    calls: list[str] = field(default_factory=list)

    async def approve(self, req: ApprovalRequest) -> ApprovalDecision:
        self.calls.append(req.tool_name)
        return self.decision

    async def clarify(self, req: Any) -> str:
        return "ok"


def _write_tool(home: Path, name: str, body: str) -> Path:
    root = home / "tools"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _deps(home: Path, hitl: Any, enabled: list[str] | None = None) -> TurnDeps:
    settings = LatticeSettings(home=home)
    registry = SqliteRegistry(settings)
    return TurnDeps(
        settings=settings,
        profile=Profile(id="default"),
        hitl=hitl,
        session=SessionStore(home / "state.db"),
        session_id="s1",
        memory=InMemoryMemory("t"),
        sqlite_registry=registry,
        sqlite_pool=SqlitePool(registry),
        mcp=McpHostManager(),
        events=NullTurnEvents(),
        workspace=home,
        enabled_tools=enabled or [],
    )


def _ctx(deps: TurnDeps) -> SimpleNamespace:
    return SimpleNamespace(
        deps=deps, max_retries=1, retries={}, model=None, usage=None, prompt=None
    )


# --- manifest parsing ------------------------------------------------------


def test_scan_parses_path_and_code_handlers(tmp_path: Path) -> None:
    script = tmp_path / "skills" / "demo" / "scripts" / "hi.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('hi')\n", encoding="utf-8")
    _write_tool(
        tmp_path,
        "from_path",
        "name: from_path\ndescription: p\nlanguage: python\nhandler:\n  path: skills/demo/scripts/hi.py\n",
    )
    _write_tool(
        tmp_path,
        "from_code",
        "name: from_code\ndescription: c\nlanguage: python\nhandler:\n  code: |\n    print(1)\n",
    )
    report = scan_user_tools(tmp_path)
    assert report.errors == []
    assert [s.name for s in report.specs] == ["from_code", "from_path"]


def test_scan_rejects_bad_manifests(tmp_path: Path) -> None:
    _write_tool(tmp_path, "bad", "{ this is not: valid yaml")
    _write_tool(tmp_path, "Bad_Name", "name: Bad_Name\nhandler:\n  code: print(1)\n")
    _write_tool(tmp_path, "shell", "name: shell\nhandler:\n  code: print(1)\n")
    _write_tool(
        tmp_path,
        "cobol",
        "name: cobol\nlanguage: cobol\nhandler:\n  code: print(1)\n",
    )
    _write_tool(tmp_path, "nohandler", "name: nohandler\nhandler: {}\n")
    report = scan_user_tools(tmp_path)
    assert report.specs == []
    assert len(report.errors) == 5
    joined = " | ".join(report.errors)
    assert "reserved core tool name" in joined
    assert "invalid tool name" in joined
    assert "unknown language" in joined
    assert "exactly one of path or code" in joined


def test_scan_rejects_duplicate_names(tmp_path: Path) -> None:
    _write_tool(tmp_path, "one", "name: dup\nhandler:\n  code: print(1)\n")
    _write_tool(tmp_path, "two", "name: dup\nhandler:\n  code: print(2)\n")
    report = scan_user_tools(tmp_path)
    assert [s.name for s in report.specs] == ["dup"]
    assert any("duplicate" in e for e in report.errors)


def test_handler_exactly_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ToolHandler(path="a.py", code="print(1)")
    with pytest.raises(ValueError):
        ToolHandler()


def test_digest_changes_with_handler_code() -> None:
    one = UserToolSpec(name="x", description="d", handler=ToolHandler(code="print(1)"))
    two = UserToolSpec(name="x", description="d", handler=ToolHandler(code="print(2)"))
    assert one.digest != two.digest


# --- policy / tiering ------------------------------------------------------


def test_resolve_enabled_tools_includes_user_tools(tmp_path: Path) -> None:
    settings = LatticeSettings(home=tmp_path)
    profile = Profile(id="default", tools_allow=["*"], tools_deny=[])
    enabled = resolve_enabled_tools(
        settings, profile, channel="cli", mcp=McpHostManager(), extra_tools=["greet"]
    )
    assert "user/greet" in enabled


def test_resolve_enabled_tools_honours_deny(tmp_path: Path) -> None:
    settings = LatticeSettings(home=tmp_path, tools=ToolsConfig(allow=["*"], deny=["greet"]))
    profile = Profile(id="default", tools_allow=["*"], tools_deny=[])
    enabled = resolve_enabled_tools(
        settings, profile, channel="cli", mcp=McpHostManager(), extra_tools=["greet"]
    )
    assert "user/greet" not in enabled


def test_user_tool_defaults_eager(tmp_path: Path) -> None:
    from lattice.agent_app import _user_tool_tier
    from lattice.config import ToolTier

    settings = LatticeSettings(home=tmp_path)
    spec = UserToolSpec(name="greet", description="d", handler=ToolHandler(code="print(1)"))
    assert _user_tool_tier(settings, spec) is ToolTier.EAGER


def test_user_tool_can_be_deferred(tmp_path: Path) -> None:
    from lattice.agent_app import _user_tool_tier
    from lattice.config import ToolTier

    settings = LatticeSettings(home=tmp_path, tools=ToolsConfig(cold=["greet"]))
    spec = UserToolSpec(name="greet", description="d", handler=ToolHandler(code="print(1)"))
    assert _user_tool_tier(settings, spec) is ToolTier.COLD

    # A single UserToolset still lists the tool; deferral is applied by the
    # builder that wraps cold specs. Verify the schema is exposed.
    toolset = UserToolset([spec])
    ctx = _ctx(_deps(tmp_path, RecordingHitl(), enabled=["user/greet"]))
    tools = asyncio.run(toolset.get_tools(ctx))
    assert "user__greet" in tools


def test_cold_user_tool_is_deferred_from_first_request(tmp_path: Path) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from lattice.agent_app import tool_search_capability

    settings = LatticeSettings(home=tmp_path, tools=ToolsConfig(cold=["greet"]))
    spec = UserToolSpec(name="greet", description="d", handler=ToolHandler(code="print(1)"))
    toolset = build_core_toolset(settings, McpHostManager(), user_tools=[spec])
    deps = _deps(tmp_path, RecordingHitl(), enabled=["user/greet"])
    captured: dict[str, list[str]] = {}

    async def fn(messages, info):  # type: ignore[no-untyped-def]
        captured["eager"] = [t.name for t in info.function_tools]
        captured["deferred"] = [
            t.name
            for t in info.model_request_parameters.function_tools
            if getattr(t, "defer_loading", False)
        ]
        return ModelResponse(parts=[TextPart("ok")])

    agent: Agent[TurnDeps, str] = Agent(
        FunctionModel(fn),
        deps_type=TurnDeps,
        system_prompt="t",
        toolsets=[toolset],
        capabilities=[tool_search_capability()],
    )
    asyncio.run(agent.run("hi", deps=deps))
    assert "user__greet" in captured["deferred"]
    assert "user__greet" not in captured["eager"]


def test_toolset_cache_key_tracks_handler_digest(tmp_path: Path) -> None:
    settings = LatticeSettings(home=tmp_path)
    mcp = McpHostManager()
    one = UserToolSpec(name="x", description="d", handler=ToolHandler(code="print(1)"))
    two = UserToolSpec(name="x", description="d", handler=ToolHandler(code="print(2)"))
    k1 = _toolset_cache_key(
        settings, mcp, exclude=frozenset(), filter_policy=True, user_tools=[one]
    )
    k2 = _toolset_cache_key(
        settings, mcp, exclude=frozenset(), filter_policy=True, user_tools=[two]
    )
    assert k1 != k2


def test_toolset_exposes_declared_schema(tmp_path: Path) -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    spec = UserToolSpec(
        name="greet", description="Say hi", parameters=schema, handler=ToolHandler(code="print(1)")
    )
    toolset = UserToolset([spec])
    ctx = _ctx(_deps(tmp_path, RecordingHitl()))
    tools = asyncio.run(toolset.get_tools(ctx))
    assert set(tools) == {"user__greet"}
    assert tools["user__greet"].tool_def.description == "Say hi"
    assert tools["user__greet"].tool_def.parameters_json_schema == schema


def test_validate_args_required_and_types() -> None:
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}, "s": {"type": "string"}},
        "required": ["n"],
    }
    assert validate_args(schema, {"n": 1}) is None
    assert "missing required" in (validate_args(schema, {}) or "")
    assert "n must be integer" in (validate_args(schema, {"n": "x"}) or "")
    assert "n must be integer" in (validate_args(schema, {"n": True}) or "")
    assert "s must be string" in (validate_args(schema, {"n": 1, "s": 2}) or "")


# --- call_tool execution ---------------------------------------------------


def test_benign_handler_runs_without_approval(tmp_path: Path) -> None:
    _write_tool(
        tmp_path,
        "greet",
        "name: greet\ndescription: d\nlanguage: python\n"
        "parameters:\n  type: object\n  properties:\n    name: {type: string}\n"
        "  required: [name]\n"
        f"handler:\n  code: |\n{_indent(BENIGN)}",
    )
    spec = scan_user_tools(tmp_path).specs[0]
    hitl = RecordingHitl(ApprovalDecision.DENY)
    deps = _deps(tmp_path, hitl, enabled=["user/greet"])
    deps.user_tools = [spec]
    toolset = _guarded(UserToolset([spec]))
    out = asyncio.run(toolset.call_tool("user__greet", {"name": "Ada"}, _ctx(deps), None))  # type: ignore[arg-type]
    assert "greet Ada" in out
    assert "tool greet" in out
    assert '"name": "Ada"' in out
    assert hitl.calls == []


def test_path_handler_runs_from_its_own_directory(tmp_path: Path) -> None:
    """A path handler runs by path so __file__ and sibling imports resolve."""
    script_dir = tmp_path / "skills" / "demo" / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "helper.py").write_text("VALUE = 'from-sibling'\n", encoding="utf-8")
    (script_dir / "hi.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "import helper\n"
        "print('file', Path(__file__).name)\n"
        "print('sibling', helper.VALUE)\n"
        "print('name', json.load(sys.stdin)['name'])\n",
        encoding="utf-8",
    )
    _write_tool(
        tmp_path,
        "from_path",
        "name: from_path\ndescription: p\nlanguage: python\n"
        "parameters:\n  type: object\n  properties:\n    name: {type: string}\n"
        "  required: [name]\n"
        "handler:\n  path: skills/demo/scripts/hi.py\n",
    )
    spec = scan_user_tools(tmp_path).specs[0]
    deps = _deps(tmp_path, RecordingHitl(ApprovalDecision.DENY), enabled=["user/from_path"])
    deps.user_tools = [spec]
    toolset = _guarded(UserToolset([spec]))
    out = asyncio.run(toolset.call_tool("user__from_path", {"name": "Ada"}, _ctx(deps), None))  # type: ignore[arg-type]
    assert "file hi.py" in out
    assert "sibling from-sibling" in out
    assert "name Ada" in out


def test_dangerous_handler_triggers_hitl(tmp_path: Path) -> None:
    _write_tool(
        tmp_path,
        "outer",
        "name: outer\ndescription: d\nlanguage: python\n"
        "handler:\n  code: |\n    import subprocess\n    print('nope')\n",
    )
    spec = scan_user_tools(tmp_path).specs[0]
    hitl = RecordingHitl(ApprovalDecision.DENY)
    deps = _deps(tmp_path, hitl, enabled=["user/outer"])
    deps.user_tools = [spec]
    toolset = _guarded(UserToolset([spec]))
    out = asyncio.run(toolset.call_tool("user__outer", {}, _ctx(deps), None))  # type: ignore[arg-type]
    assert out == "denied: deny"
    assert hitl.calls == ["user/outer"]


def test_invalid_args_are_rejected_before_running(tmp_path: Path) -> None:
    _write_tool(
        tmp_path,
        "greet",
        "name: greet\ndescription: d\nlanguage: python\n"
        "parameters:\n  type: object\n  required: [name]\n"
        "handler:\n  code: |\n    print('ran')\n",
    )
    spec = scan_user_tools(tmp_path).specs[0]
    hitl = RecordingHitl(ApprovalDecision.APPROVE)
    deps = _deps(tmp_path, hitl, enabled=["user/greet"])
    deps.user_tools = [spec]
    toolset = _guarded(UserToolset([spec]))
    out = asyncio.run(toolset.call_tool("user__greet", {}, _ctx(deps), None))  # type: ignore[arg-type]
    assert "missing required" in out
    assert "ran" not in out
    assert hitl.calls == []


def _indent(text: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "".join(f"{pad}{line}\n" for line in text.splitlines())


def test_user_tool_reaches_model_and_executes(tmp_path: Path) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    spec = UserToolSpec(
        name="greet",
        description="Say hi",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=ToolHandler(code="import json, sys\nprint('hi', json.load(sys.stdin)['name'])\n"),
    )
    settings = LatticeSettings(home=tmp_path)
    deps = _deps(tmp_path, RecordingHitl(ApprovalDecision.APPROVE), enabled=["user/greet"])
    deps.user_tools = [spec]
    toolset = build_core_toolset(settings, McpHostManager(), user_tools=[spec])

    seen: dict[str, list[str]] = {}
    state = {"calls": 0}

    async def fn(messages, info):  # type: ignore[no-untyped-def]
        seen["names"] = [t.name for t in info.function_tools]
        if state["calls"] == 0:
            state["calls"] = 1
            return ModelResponse(parts=[ToolCallPart("user__greet", {"name": "Ada"})])
        return ModelResponse(parts=[TextPart("done")])

    agent: Agent[TurnDeps, str] = Agent(
        FunctionModel(fn), deps_type=TurnDeps, system_prompt="t", toolsets=[toolset]
    )
    result = asyncio.run(agent.run("hi", deps=deps))
    assert "user__greet" in seen["names"]
    assert str(result.output) == "done"
