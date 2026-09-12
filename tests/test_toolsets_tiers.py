"""Eager/cold tiering, policy filtering, and remove_path coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart

from lattice.config import LatticeSettings, ToolTier
from lattice.profiles.load import Profile
from lattice.tools.agent import build_toolsets, default_eager_names, resolve_tier
from lattice.tools.files import remove_path


def _all_names(toolsets) -> set[str]:
    """Tool names reachable in a toolset tree, unwrapping wrappers."""

    def _collect(ts: object) -> set[str]:
        out = set(getattr(ts, "tools", {}).keys())
        inner = getattr(ts, "wrapped", None)
        if inner is not None:
            out |= _collect(inner)
        return out

    names: set[str] = set()
    for ts in toolsets:
        names |= _collect(ts)
    return names


def _eager_names(toolsets) -> set[str]:
    """Names exposed without discovery: the first (eager) toolset only."""
    return set(getattr(toolsets[0], "tools", {}).keys())


# --- tier resolution -------------------------------------------------------


def test_default_tiers_partition_all_core_tools() -> None:
    from lattice.deps import CORE_TOOL_NAMES

    eager = set(default_eager_names())
    assert "read_file" in eager
    assert "shell" in eager
    assert "remove_path" in eager
    assert "delegate" in eager
    # Cold by default.
    assert "browser_interact" not in eager
    assert "generate_pdf" not in eager
    assert "sqlite_execute" not in eager
    # Every tool lands in exactly one tier.
    assert eager <= set(CORE_TOOL_NAMES)


def test_config_glob_overrides_module_tier() -> None:
    # `web_*` is eager by default; force it cold.
    assert resolve_tier("web_search", eager=[], cold=[]) is ToolTier.EAGER
    assert resolve_tier("web_search", eager=[], cold=["web_*"]) is ToolTier.COLD
    # `browser_*` is cold by default; force it eager.
    assert resolve_tier("browser_interact", eager=[], cold=[]) is ToolTier.COLD
    assert resolve_tier("browser_interact", eager=["browser_*"], cold=[]) is ToolTier.EAGER
    # ``cold`` wins when both match.
    assert resolve_tier("web_search", eager=["web_*"], cold=["web_*"]) is ToolTier.COLD


def test_cold_tools_are_deferred_not_absent() -> None:
    toolsets = build_toolsets()
    eager = _eager_names(toolsets)
    everything = _all_names(toolsets)
    # Cold tools exist in the tree but are not in the eager set.
    assert "generate_chart" in everything
    assert "generate_chart" not in eager
    # The cold toolset is wrapped for deferral.
    assert len(toolsets) == 2
    assert toolsets[1].__class__.__name__ == "DeferredLoadingToolset"


def test_exclude_drops_tool_entirely() -> None:
    toolsets = build_toolsets(exclude=frozenset({"delegate", "shell"}))
    names = _all_names(toolsets)
    assert "delegate" not in names
    assert "shell" not in names
    assert "read_file" in names


# --- what actually reaches the model --------------------------------------


def _capture_tool_names(
    settings: LatticeSettings, enabled: list[str], *, with_search: bool = False
) -> dict:
    """Run a FunctionModel agent and record the tools the model was offered.

    ``with_search`` adds the tool-search capability, which is what emits the
    ``search_tools`` affordance for deferred tools.
    """
    from lattice.agent_app import build_core_toolset
    from lattice.deps import TurnDeps
    from lattice.mcp import McpHostManager

    core = build_core_toolset(settings, McpHostManager())
    captured: dict = {}

    async def fn(messages, info):
        captured["function_tools"] = [t.name for t in info.function_tools]
        # The deferred corpus is what `search_tools` can reveal, so a tool absent
        # here is undiscoverable no matter what the model searches for.
        captured["deferred_tools"] = [
            t.name
            for t in info.model_request_parameters.function_tools
            if getattr(t, "defer_loading", False)
        ]
        return ModelResponse(parts=[TextPart("ok")])

    from pydantic_ai import Agent

    capabilities = []
    if with_search:
        from lattice.agent_app import tool_search_capability

        capabilities = [tool_search_capability()]

    agent = Agent(
        FunctionModel(fn),
        deps_type=TurnDeps,
        system_prompt="t",
        toolsets=[core],
        capabilities=capabilities,
    )
    import asyncio

    deps = _deps_for(settings, enabled)
    asyncio.run(agent.run("hi", deps=deps))
    return captured


def _deps_for(settings: LatticeSettings, enabled: list[str]):
    from lattice.deps import TurnDeps
    from lattice.events import NullTurnEvents
    from lattice.hitl import AutoApproveHitl
    from lattice.mcp import McpHostManager
    from lattice.session import SessionStore
    from lattice.sqlite import SqlitePool, SqliteRegistry

    registry = SqliteRegistry(settings)

    class _Mem:
        async def search(self, *a, **k):
            return []

        async def add(self, *a, **k):
            return "x"

        async def update(self, *a, **k):
            return None

        async def forget(self, *a, **k):
            return None

        async def sync_turn(self, *a, **k):
            return None

    return TurnDeps(
        settings=settings,
        profile=Profile(id="default"),
        hitl=AutoApproveHitl(approve_all=True),
        session=SessionStore(settings.home / "state.db"),
        session_id="s",
        memory=_Mem(),  # type: ignore[arg-type]
        sqlite_registry=registry,
        sqlite_pool=SqlitePool(registry),
        mcp=McpHostManager(),
        events=NullTurnEvents(),
        workspace=settings.home,
        enabled_tools=enabled,
    )


def test_policy_filter_hides_disabled_tools(tmp_path: Path) -> None:
    settings = LatticeSettings(home=tmp_path)
    captured = _capture_tool_names(settings, ["read_file"])
    offered = captured["function_tools"]
    assert "read_file" in offered
    assert "shell" not in offered
    assert "write_file" not in offered


def test_filter_applies_to_cold_tools_too(tmp_path: Path) -> None:
    settings = LatticeSettings(home=tmp_path)
    captured = _capture_tool_names(settings, ["generate_chart"])
    offered = captured["function_tools"]
    # A cold tool is deferred, so it is absent from the first request even when
    # policy allows it. (A denied cold tool is likewise never offered.)
    assert "generate_chart" not in offered
    assert "shell" not in offered, "denied eager tool leaked"
    assert "read_file" not in offered, "denied eager tool leaked"


def test_eager_tools_all_offered_when_enabled(tmp_path: Path) -> None:
    settings = LatticeSettings(home=tmp_path)
    from lattice.tools.agent import default_eager_names

    captured = _capture_tool_names(settings, default_eager_names())
    offered = set(captured["function_tools"])
    assert offered == set(default_eager_names())


def test_wire_reduction_is_real(tmp_path: Path) -> None:
    """The headline goal: far fewer schemas on the first request than registered."""
    from lattice.agent_app import resolve_enabled_tools
    from lattice.deps import CORE_TOOL_NAMES
    from lattice.mcp import McpHostManager
    from lattice.tools.agent import default_eager_names

    settings = LatticeSettings(home=tmp_path)
    mcp = McpHostManager()
    enabled = resolve_enabled_tools(settings, Profile(id="default"), channel="cli", mcp=mcp)
    assert set(enabled) == set(CORE_TOOL_NAMES)

    captured = _capture_tool_names(settings, enabled)
    offered = set(captured["function_tools"])
    assert offered == set(default_eager_names())
    assert offered < set(CORE_TOOL_NAMES)
    # Cold tools exist in the tree, deferred rather than dropped.
    assert len(offered) == len(default_eager_names())


def test_denied_cold_tool_is_absent_from_discovery_corpus(tmp_path: Path) -> None:
    """Discovery must not become a policy bypass.

    A cold tool whose policy is denied has to be missing from the deferred
    corpus, because that corpus is exactly what ``search_tools`` can reveal.
    """
    settings = LatticeSettings(home=tmp_path)
    captured = _capture_tool_names(settings, ["read_file", "generate_chart"])
    corpus = set(captured["deferred_tools"])
    assert "generate_chart" in corpus, "allowed cold tool should be discoverable"
    assert "browser_snapshot" not in corpus, "denied cold tool leaked into the search corpus"
    assert "sqlite_execute" not in corpus, "denied cold tool leaked into the search corpus"


def test_search_tools_only_offered_with_a_nonempty_corpus(tmp_path: Path) -> None:
    """`search_tools` is pointless without something to find, so it is omitted."""
    settings = LatticeSettings(home=tmp_path)

    # Only eager tools allowed: nothing is deferred, so no discovery affordance.
    from lattice.tools.agent import default_eager_names

    bare = _capture_tool_names(settings, default_eager_names(), with_search=True)
    assert "search_tools" not in bare["function_tools"]
    assert bare["deferred_tools"] == []

    # One allowed cold tool is enough to make discovery worth advertising.
    rich = _capture_tool_names(settings, ["read_file", "generate_chart"], with_search=True)
    assert "search_tools" in rich["function_tools"]
    assert rich["deferred_tools"] == ["generate_chart"]


def test_calling_a_denied_tool_never_executes(tmp_path: Path) -> None:
    """Last line of defence: a denied tool is unrunnable even if the model insists.

    The framework rejects the call because the tool is not in the resolved
    toolset, so no side effect reaches disk.
    """
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    from lattice.agent_app import build_core_toolset, tool_search_capability
    from lattice.mcp import McpHostManager

    settings = LatticeSettings(home=tmp_path)
    deps = _deps_for(settings, ["read_file"])  # write_file denied

    async def always_calls_denied(messages, info):
        return ModelResponse(parts=[ToolCallPart("write_file", {"path": "x.txt", "content": "hi"})])

    agent = Agent(
        FunctionModel(always_calls_denied),
        deps_type=type(deps),
        system_prompt="t",
        toolsets=[build_core_toolset(settings, McpHostManager())],
        capabilities=[tool_search_capability()],
    )
    with pytest.raises(Exception):
        asyncio.run(agent.run("write it", deps=deps))
    # The tool body never ran: nothing was written.
    assert not (tmp_path / "x.txt").exists()


# --- remove_path -----------------------------------------------------------


def test_remove_path_removes_file(tmp_path: Path) -> None:
    import asyncio

    target = tmp_path / "a.txt"
    target.write_text("hi")
    out = asyncio.run(remove_path("a.txt", workspace=tmp_path, home=tmp_path))
    assert "removed file" in out
    assert not target.exists()


def test_remove_path_missing_ok(tmp_path: Path) -> None:
    import asyncio

    out = asyncio.run(remove_path("nope.txt", workspace=tmp_path, home=tmp_path, missing_ok=True))
    assert "nothing to remove" in out
    with pytest.raises(FileNotFoundError):
        asyncio.run(remove_path("nope.txt", workspace=tmp_path, home=tmp_path))


def test_remove_path_refuses_nonempty_dir_without_recursive(tmp_path: Path) -> None:
    import asyncio

    d = tmp_path / "d"
    d.mkdir()
    (d / "f.txt").write_text("x")
    with pytest.raises(ValueError, match="not empty"):
        asyncio.run(remove_path("d", workspace=tmp_path, home=tmp_path))


def test_remove_path_removes_dir_recursively(tmp_path: Path) -> None:
    import asyncio

    d = tmp_path / "d"
    (d / "nested").mkdir(parents=True)
    (d / "nested" / "f.txt").write_text("x")
    out = asyncio.run(remove_path("d", workspace=tmp_path, home=tmp_path, recursive=True))
    assert "removed directory" in out
    assert not d.exists()


def test_remove_path_refuses_outside_workspace(tmp_path: Path) -> None:
    import asyncio

    with pytest.raises(PermissionError):
        asyncio.run(remove_path("../escape.txt", workspace=tmp_path, home=tmp_path))


def test_remove_path_refuses_denied_secret_path(tmp_path: Path) -> None:
    import asyncio

    (tmp_path / ".env").write_text("SECRET=1")
    with pytest.raises(PermissionError):
        asyncio.run(remove_path(".env", workspace=tmp_path, home=tmp_path))
    assert (tmp_path / ".env").exists()


def test_remove_path_refuses_workspace_root_and_state(tmp_path: Path) -> None:
    import asyncio

    with pytest.raises(PermissionError):
        asyncio.run(remove_path(".", workspace=tmp_path, home=tmp_path, recursive=True))
    (tmp_path / "state.db").write_text("db")
    with pytest.raises(PermissionError):
        asyncio.run(remove_path("state.db", workspace=tmp_path, home=tmp_path))
    assert (tmp_path / "state.db").exists()


def test_remove_path_removes_symlink_not_target(tmp_path: Path) -> None:
    import asyncio

    real = tmp_path / "real.txt"
    real.write_text("keep me")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    out = asyncio.run(remove_path("link.txt", workspace=tmp_path, home=tmp_path))
    assert "removed" in out
    assert not link.exists()
    assert real.exists()  # target untouched


def test_remove_path_symlink_escape_does_not_delete_outside_target(tmp_path: Path) -> None:
    """A link pointing outside the jail must be removed as a link, not followed."""
    import asyncio

    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("must survive")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    link = workspace / "escape.txt"
    link.symlink_to(outside)
    try:
        out = asyncio.run(remove_path("escape.txt", workspace=workspace, home=workspace))
        assert "removed" in out
        assert not link.exists()
        assert outside.exists(), "symlink target outside the jail was deleted"
    finally:
        outside.unlink(missing_ok=True)


def test_remove_path_is_hitl_gated(tmp_path: Path) -> None:
    import asyncio

    from pydantic_ai import RunContext

    from lattice.deps import maybe_approve
    from lattice.hitl import ApprovalDecision, ApprovalRequest

    class _Deny:
        async def approve(self, req: ApprovalRequest) -> ApprovalDecision:
            return ApprovalDecision.DENY

        async def clarify(self, req):
            return ""

    target = tmp_path / "x.txt"
    target.write_text("hi")
    deps = _deps_for(LatticeSettings(home=tmp_path), ["remove_path"])
    deps.hitl = _Deny()  # type: ignore[assignment]
    ctx = RunContext(deps=deps, model=None, usage=None, prompt=None)  # type: ignore[arg-type]
    out = asyncio.run(maybe_approve(ctx, "remove_path", "x.txt", path="x.txt"))
    assert out == "denied: deny"
    assert target.exists()
