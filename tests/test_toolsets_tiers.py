"""Eager/cold tiering, policy filtering, and remove_path coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from lattice.config import LatticeSettings, ToolTier
from lattice.profiles.load import Profile
from lattice.tool_names import wire_name
from lattice.tools.files import remove_path
from lattice.tools.groups import build_toolsets, default_eager_names, resolve_tier


def _all_names(toolsets) -> set[str]:
    """Model-facing (wire) names reachable in a toolset tree, unwrapping wrappers."""

    def _collect(ts: object) -> set[str]:
        out = set(getattr(ts, "tools", {}).keys())
        if getattr(ts, "group", None):
            return out
        inner = getattr(ts, "wrapped", None)
        if inner is not None:
            out |= _collect(inner)
        return out

    names: set[str] = set()
    for ts in toolsets:
        names |= _collect(ts)
    return names


def _eager_names(toolsets) -> set[str]:
    """Names exposed without discovery: every non-deferred toolset."""
    from pydantic_ai.toolsets import DeferredLoadingToolset

    names: set[str] = set()
    for ts in toolsets:
        if isinstance(ts, DeferredLoadingToolset):
            continue
        names |= set(getattr(ts, "tools", {}).keys())
    return names


# --- tier resolution -------------------------------------------------------


def test_default_tiers_partition_all_core_tools() -> None:
    from lattice.deps import CORE_TOOL_NAMES

    eager = set(default_eager_names())
    assert "files/read" in eager
    assert "files/shell" in eager
    assert "files/remove" in eager
    assert "web/search" in eager
    # Cold by default.
    assert "browser/interact" not in eager
    assert "media/pdf" not in eager
    assert "sqlite/execute" not in eager
    # Every tool lands in exactly one tier.
    assert eager <= set(CORE_TOOL_NAMES)


def test_config_glob_overrides_module_tier() -> None:
    # `web/*` is eager by default; force it cold.
    assert resolve_tier("web/search", eager=[], cold=[]) is ToolTier.EAGER
    assert resolve_tier("web/search", eager=[], cold=["web/*"]) is ToolTier.COLD
    # Legacy globs and flat names still match.
    assert resolve_tier("web/search", eager=[], cold=["web_*"]) is ToolTier.COLD
    assert resolve_tier("web/search", eager=[], cold=["web_search"]) is ToolTier.COLD
    # `browser/*` is cold by default; force it eager.
    assert resolve_tier("browser/interact", eager=[], cold=[]) is ToolTier.COLD
    assert resolve_tier("browser/interact", eager=["browser/*"], cold=[]) is ToolTier.EAGER
    # ``cold`` wins when both match.
    assert resolve_tier("web/search", eager=["web/*"], cold=["web/*"]) is ToolTier.COLD


def test_cold_tools_are_deferred_not_absent() -> None:
    from pydantic_ai.toolsets import DeferredLoadingToolset

    toolsets = build_toolsets()
    eager = _eager_names(toolsets)
    everything = _all_names(toolsets)
    # Cold tools exist in the tree but are not in the eager set.
    assert "media__chart" in everything
    assert "media__chart" not in eager
    # At least one group is wrapped for deferral.
    assert any(isinstance(ts, DeferredLoadingToolset) for ts in toolsets)


def test_exclude_drops_tool_entirely() -> None:
    toolsets = build_toolsets(exclude=frozenset({"web/search", "files/shell"}))
    names = _all_names(toolsets)
    assert "web__search" not in names
    assert "files__shell" not in names
    assert "files__read" in names


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
    captured = _capture_tool_names(settings, ["files/read"])
    offered = captured["function_tools"]
    assert "files__read" in offered
    assert "files__shell" not in offered
    assert "files__write" not in offered


def test_filter_applies_to_cold_tools_too(tmp_path: Path) -> None:
    settings = LatticeSettings(home=tmp_path)
    captured = _capture_tool_names(settings, ["media/chart"])
    offered = captured["function_tools"]
    # A cold tool is deferred, so it is absent from the first request even when
    # policy allows it. (A denied cold tool is likewise never offered.)
    assert "media__chart" not in offered
    assert "files__shell" not in offered, "denied eager tool leaked"
    assert "files__read" not in offered, "denied eager tool leaked"


def test_eager_tools_all_offered_when_enabled(tmp_path: Path) -> None:
    settings = LatticeSettings(home=tmp_path)
    from lattice.tools.groups import default_eager_names

    captured = _capture_tool_names(settings, default_eager_names())
    offered = set(captured["function_tools"])
    assert offered == {wire_name(name) for name in default_eager_names()}


def test_wire_reduction_is_real(tmp_path: Path) -> None:
    """The headline goal: far fewer schemas on the first request than registered."""
    from lattice.agent_app import resolve_enabled_tools
    from lattice.deps import CORE_TOOL_NAMES
    from lattice.mcp import McpHostManager
    from lattice.tools.groups import default_eager_names

    settings = LatticeSettings(home=tmp_path)
    mcp = McpHostManager()
    enabled = resolve_enabled_tools(settings, Profile(id="default"), channel="cli", mcp=mcp)
    assert set(enabled) == set(CORE_TOOL_NAMES)

    captured = _capture_tool_names(settings, enabled)
    offered = set(captured["function_tools"])
    eager_wire = {wire_name(name) for name in default_eager_names()}
    assert offered == eager_wire
    assert offered < {wire_name(name) for name in CORE_TOOL_NAMES}
    # Cold tools exist in the tree, deferred rather than dropped.
    assert len(offered) == len(eager_wire)


def test_denied_cold_tool_is_absent_from_discovery_corpus(tmp_path: Path) -> None:
    """Discovery must not become a policy bypass.

    A cold tool whose policy is denied has to be missing from the deferred
    corpus, because that corpus is exactly what ``search_tools`` can reveal.
    """
    settings = LatticeSettings(home=tmp_path)
    captured = _capture_tool_names(settings, ["files/read", "media/chart"])
    corpus = set(captured["deferred_tools"])
    assert "media__chart" in corpus, "allowed cold tool should be discoverable"
    assert "browser__snapshot" not in corpus, "denied cold tool leaked into the search corpus"
    assert "sqlite__execute" not in corpus, "denied cold tool leaked into the search corpus"


def test_search_tools_only_offered_with_a_nonempty_corpus(tmp_path: Path) -> None:
    """`search_tools` is pointless without something to find, so it is omitted."""
    settings = LatticeSettings(home=tmp_path)

    # Only eager tools allowed: nothing is deferred, so no discovery affordance.
    from lattice.tools.groups import default_eager_names

    bare = _capture_tool_names(settings, default_eager_names(), with_search=True)
    assert "search_tools" not in bare["function_tools"]
    assert bare["deferred_tools"] == []

    # One allowed cold tool is enough to make discovery worth advertising.
    rich = _capture_tool_names(settings, ["files/read", "media/chart"], with_search=True)
    assert "search_tools" in rich["function_tools"]
    assert rich["deferred_tools"] == ["media__chart"]


# --- BM25 search strategy --------------------------------------------------


def test_tool_search_capability_maps_local_strategies() -> None:
    from lattice.agent_app import tool_search_capability

    bm25 = tool_search_capability("bm25")
    assert callable(bm25.strategy), "bm25 must map to the local callable, never the native string"
    assert bm25.strategy != "bm25"

    assert tool_search_capability("keywords").strategy == "keywords"
    # Unknown values fall back to the BM25 callable.
    assert callable(tool_search_capability("nonsense").strategy)
    # Backward-compatible no-arg call defaults to BM25.
    assert callable(tool_search_capability().strategy)


def test_tool_search_capability_carries_custom_description() -> None:
    from lattice.agent_app import tool_search_capability

    bm25 = tool_search_capability("bm25", tool_description="X", min_ratio=0.5)
    assert callable(bm25.strategy)
    assert bm25.tool_description == "X"

    keywords = tool_search_capability("keywords", tool_description="Y")
    assert keywords.strategy == "keywords"
    assert keywords.tool_description == "Y"


def test_build_search_description_lists_enabled_cold_core_tools(tmp_path: Path) -> None:
    from lattice.agent_app import build_search_description
    from lattice.mcp import McpHostManager

    settings = LatticeSettings(home=tmp_path)
    enabled = ["files/read", "files/shell", "browser/snapshot", "media/chart", "sqlite/execute"]
    desc = build_search_description(enabled, settings, McpHostManager())

    assert "Deferred tools available via search_tools:" in desc
    # Cold names, sorted, model-facing wire form, and only the policy-enabled ones.
    assert (
        desc.index("browser__snapshot") < desc.index("media__chart") < desc.index("sqlite__execute")
    )
    assert "sqlite__backup" not in desc, "policy-denied cold tool leaked into the manifest"
    # Eager tools are not advertised as discoverable.
    assert "files__read" not in desc
    assert "files__shell" not in desc
    # Byte-stable across calls.
    assert build_search_description(enabled, settings, McpHostManager()) == desc


def test_build_search_description_unchanged_without_cold_tools(tmp_path: Path) -> None:
    from pydantic_ai.toolsets._tool_search import _DEFAULT_TOOL_DESCRIPTION

    from lattice.agent_app import build_search_description
    from lattice.mcp import McpHostManager

    settings = LatticeSettings(home=tmp_path)
    desc = build_search_description(default_eager_names(), settings, McpHostManager())
    assert desc == _DEFAULT_TOOL_DESCRIPTION
    assert "search_tools" not in desc


def test_build_search_description_mentions_deferred_mcp(tmp_path: Path) -> None:
    from typing import Any, cast

    from lattice.agent_app import build_search_description
    from lattice.config import McpDeferMode, ToolsConfig
    from lattice.mcp import McpHostManager

    class _FakeMcp:
        def enabled_tools(self) -> list[Any]:
            return [object()]

    defer = LatticeSettings(home=tmp_path, tools=ToolsConfig(mcp_defer=McpDeferMode.ALWAYS))
    kept = LatticeSettings(home=tmp_path, tools=ToolsConfig(mcp_defer=McpDeferMode.NEVER))
    enabled = ["files/read", "media/chart"]

    assert "Also deferred MCP tools." in build_search_description(
        enabled, defer, cast(McpHostManager, _FakeMcp())
    )
    assert "Also deferred MCP tools." not in build_search_description(
        enabled, kept, cast(McpHostManager, _FakeMcp())
    )


def test_build_search_description_mentions_deferred_user_tools(tmp_path: Path) -> None:
    from lattice.agent_app import build_search_description
    from lattice.config import ToolsConfig
    from lattice.mcp import McpHostManager
    from lattice.tools.user_tools import ToolHandler, UserToolSpec

    spec = UserToolSpec(name="my_tool", handler=ToolHandler(code="print(1)"))
    enabled = ["files/read", "media/chart"]
    cold = LatticeSettings(home=tmp_path, tools=ToolsConfig(cold=["my_tool"]))
    eager = LatticeSettings(home=tmp_path)

    assert "Also deferred user tools." in build_search_description(
        enabled, cold, McpHostManager(), [spec]
    )
    assert "Also deferred user tools." not in build_search_description(
        enabled, eager, McpHostManager(), [spec]
    )


def test_search_min_ratio_defaults_and_bounds(tmp_path: Path) -> None:
    from pydantic import ValidationError

    from lattice.config import ToolsConfig

    assert LatticeSettings(home=tmp_path).tools.search_min_ratio == 0.35
    zero = LatticeSettings(home=tmp_path, tools=ToolsConfig(search_min_ratio=0.0))
    assert zero.tools.search_min_ratio == 0.0
    with pytest.raises(ValidationError):
        ToolsConfig(search_min_ratio=1.5)
    with pytest.raises(ValidationError):
        ToolsConfig(search_min_ratio=-0.1)


def test_bm25_search_fn_reveals_cold_tool_end_to_end(tmp_path: Path) -> None:
    """The callable is wired through the agent, not just unit-tested."""
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart

    from lattice.agent_app import build_core_toolset, tool_search_capability
    from lattice.mcp import McpHostManager

    settings = LatticeSettings(home=tmp_path)
    deps = _deps_for(settings, ["files/read", "media/chart"])
    calls = {"n": 0}

    async def fn(messages, info):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["chart"]})])
        return ModelResponse(parts=[TextPart("done")])

    agent: Agent[type(deps), str] = Agent(  # type: ignore[valid-type]
        FunctionModel(fn),
        deps_type=type(deps),
        system_prompt="t",
        toolsets=[build_core_toolset(settings, McpHostManager())],
        capabilities=[tool_search_capability("bm25")],
    )
    result = asyncio.run(agent.run("hi", deps=deps))

    returns = [
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == "search_tools"
    ]
    assert returns, "search_tools was never executed"
    from typing import Any, cast

    content = cast("dict[str, Any]", returns[0].content)
    assert [match["name"] for match in content["discovered_tools"]] == ["media__chart"]


def test_revealed_tool_is_appended_after_eager_tools(tmp_path: Path) -> None:
    """Reveals extend the cached prefix instead of shifting eager tool positions."""
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

    from lattice.agent_app import build_core_toolset, tool_search_capability
    from lattice.mcp import McpHostManager
    from lattice.tools.user_tools import ToolHandler, UserToolSpec

    # An eager *user* tool is the regression trap: before the reorder it was
    # appended after the deferred core block, so a revealed cold tool landed
    # ahead of it instead of after every eager tool.
    user_spec = UserToolSpec(name="my_eager_tool", handler=ToolHandler(code="print(1)"))

    settings = LatticeSettings(home=tmp_path)
    deps = _deps_for(settings, [*default_eager_names(), "user/my_eager_tool", "media/chart"])
    offered: list[list[str]] = []
    calls = {"n": 0}

    async def fn(messages, info):  # type: ignore[no-untyped-def]
        offered.append([t.name for t in info.function_tools])
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["chart"]})])
        return ModelResponse(parts=[TextPart("done")])

    agent: Agent[type(deps), str] = Agent(  # type: ignore[valid-type]
        FunctionModel(fn),
        deps_type=type(deps),
        system_prompt="t",
        toolsets=[build_core_toolset(settings, McpHostManager(), user_tools=[user_spec])],
        capabilities=[tool_search_capability("bm25")],
    )
    asyncio.run(agent.run("hi", deps=deps))

    assert len(offered) >= 2, f"expected a search round-trip, got {len(offered)} request(s)"
    first, second = offered[0], offered[1]
    assert "search_tools" in first
    assert "media__chart" not in first, "cold tool leaked into the first request"
    assert "user__my_eager_tool" in first, "eager user tool missing from the first request"
    assert "media__chart" in second, "revealed tool missing from the follow-up request"

    eager_wire = [wire_name(name) for name in default_eager_names()] + ["user__my_eager_tool"]
    eager_positions = [second.index(name) for name in eager_wire if name in second]
    assert eager_positions
    assert second.index("media__chart") > max(eager_positions), (
        "reveal interleaved with eager tools"
    )
    assert second.index("media__chart") < second.index("search_tools")


def test_calling_a_denied_tool_never_executes(tmp_path: Path) -> None:
    """Last line of defence: a denied tool is unrunnable even if the model insists.

    The framework rejects the call because the tool is not in the resolved
    toolset, so no side effect reaches disk.
    """
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    from lattice.agent_app import build_core_toolset, tool_search_capability
    from lattice.mcp import McpHostManager

    settings = LatticeSettings(home=tmp_path)
    deps = _deps_for(settings, ["files/read"])  # files/write denied

    async def always_calls_denied(messages, info):
        return ModelResponse(
            parts=[ToolCallPart("files__write", {"path": "x.txt", "content": "hi"})]
        )

    agent = Agent(
        FunctionModel(always_calls_denied),
        deps_type=type(deps),
        system_prompt="t",
        toolsets=[build_core_toolset(settings, McpHostManager())],
        capabilities=[tool_search_capability()],
    )
    with pytest.raises(UnexpectedModelBehavior):
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
    deps = _deps_for(LatticeSettings(home=tmp_path), ["files/remove"])
    deps.hitl = _Deny()  # type: ignore[assignment]
    ctx = RunContext(deps=deps, model=None, usage=None, prompt=None)  # type: ignore[arg-type]
    out = asyncio.run(maybe_approve(ctx, "remove_path", "x.txt", path="x.txt"))
    assert out == "denied: deny"
    assert target.exists()
