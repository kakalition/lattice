"""Whole-turn orchestrator routing: parser, classifier fail-safe, dispatch, bypass."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from lattice.config import LatticeSettings, ProviderConfig
from lattice.hitl import AutoApproveHitl
from lattice.models import Inbound
from lattice.orchestrator import (
    Complexity,
    RoutingDecision,
    decide_route,
    parse_decision,
)
from lattice.prompt import PromptBundle
from lattice.providers.caching import session_routing_settings
from lattice.session import SessionStore
from lattice.setup import init_home
from lattice.turn import run_turn

OPENROUTER = "https://openrouter.ai/api/v1"


# --- parse_decision --------------------------------------------------------


def test_parse_decision_valid_json() -> None:
    decision = parse_decision('{"complexity":"LOW","task":"rewrite it","reason":"simple"}')
    assert decision is not None
    assert decision.complexity is Complexity.LOW
    assert decision.task == "rewrite it"
    assert decision.reason == "simple"


def test_parse_decision_fenced_and_case_insensitive() -> None:
    text = '```json\n{"complexity":"high","task":"t","reason":"r"}\n```'
    decision = parse_decision(text)
    assert decision is not None
    assert decision.complexity is Complexity.HIGH


def test_parse_decision_surrounding_prose() -> None:
    decision = parse_decision('route: {"complexity":"HIGH"} — done')
    assert decision is not None
    assert decision.complexity is Complexity.HIGH


def test_parse_decision_defaults_optional_fields() -> None:
    decision = parse_decision('{"complexity":"low"}')
    assert decision is not None
    assert decision.task == "" and decision.reason == ""


@pytest.mark.parametrize(
    "text",
    [None, "", "no json here", '{"complexity":"MAYBE"}', "{not json}", "[]"],
)
def test_parse_decision_invalid_returns_none(text: str | None) -> None:
    assert parse_decision(text) is None


def test_routing_decision_coerces_case() -> None:
    assert RoutingDecision(complexity="low").complexity is Complexity.LOW  # type: ignore[arg-type]


# --- decide_route ----------------------------------------------------------


def _settings(tmp_path: Path) -> LatticeSettings:
    return LatticeSettings(home=tmp_path, provider=ProviderConfig(base_url=OPENROUTER))


@pytest.mark.asyncio
async def test_decide_route_fail_safe_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lattice.orchestrator as orch

    def boom(*args: Any, **kwargs: Any) -> Agent[Any, str]:
        raise RuntimeError("classifier transport down")

    monkeypatch.setattr(orch, "_classifier_agent", boom)
    decision, usage = await decide_route(
        _settings(tmp_path), "deepseek/deepseek-v4.1-flash", "sys", "hi", session_id="s"
    )
    assert decision.complexity is Complexity.HIGH
    assert decision.reason == "classifier error"
    assert isinstance(usage, RunUsage)
    assert usage.input_tokens == 0


@pytest.mark.asyncio
async def test_decide_route_parses_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import lattice.orchestrator as orch

    async def fn(messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart('{"complexity":"low","task":"rewrite","reason":"simple"}')]
        )

    agent: Agent[Any, str] = Agent(FunctionModel(fn), system_prompt="sys")
    monkeypatch.setattr(orch, "_classifier_agent", lambda *a, **k: agent)
    decision, _ = await decide_route(
        _settings(tmp_path), "deepseek/deepseek-v4.1-flash", "sys", "hi", session_id="s"
    )
    assert decision.complexity is Complexity.LOW
    assert decision.task == "rewrite"


@pytest.mark.asyncio
async def test_decide_route_unparseable_is_high(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lattice.orchestrator as orch

    async def fn(messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(parts=[TextPart("I am not JSON")])

    agent: Agent[Any, str] = Agent(FunctionModel(fn), system_prompt="sys")
    monkeypatch.setattr(orch, "_classifier_agent", lambda *a, **k: agent)
    decision, _ = await decide_route(
        _settings(tmp_path), "deepseek/deepseek-v4.1-flash", "sys", "hi", session_id="s"
    )
    assert decision.complexity is Complexity.HIGH
    assert decision.reason == "unparseable"


# --- session routing -------------------------------------------------------


def test_session_routing_openrouter(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert session_routing_settings(settings, "sess-1") == {"extra_body": {"session_id": "sess-1"}}
    assert session_routing_settings(settings, "") == {}
    assert session_routing_settings(settings, "   ") == {}
    assert session_routing_settings(settings, "x" * 257) == {}


def test_session_routing_non_openrouter(tmp_path: Path) -> None:
    settings = LatticeSettings(
        home=tmp_path, provider=ProviderConfig(base_url="https://api.example.com/v1")
    )
    assert session_routing_settings(settings, "sess-1") == {}


def test_classifier_has_no_output_cap_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lattice.orchestrator as orch

    captured: dict[str, Any] = {}

    class _FakeAgent:
        def __init__(self, model: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(orch, "Agent", _FakeAgent)
    orch.clear_classifier_cache()
    orch._classifier_agent(_settings(tmp_path), "deepseek/deepseek-v4.1-flash", "sys", "sess")
    assert "max_tokens" not in captured["model_settings"]


def test_classifier_respects_explicit_output_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lattice.orchestrator as orch

    settings = _settings(tmp_path)
    settings.agent.orchestrator.classifier_max_tokens = 256
    captured: dict[str, Any] = {}

    class _FakeAgent:
        def __init__(self, model: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(orch, "Agent", _FakeAgent)
    orch.clear_classifier_cache()
    orch._classifier_agent(settings, "deepseek/deepseek-v4.1-flash", "sys", "sess2")
    assert captured["model_settings"]["max_tokens"] == 256


# --- prompt bytes ----------------------------------------------------------


def test_worker_prompt_excludes_classifier_protocol() -> None:
    bundle = PromptBundle(identity="I am Lattice", context="ctx", skill_index="<skills/>")
    primary = bundle.stable_system_prompt()
    worker = bundle.worker_system_prompt()
    # Primary keeps the classifier protocol; the worker must never emit it.
    assert "Orchestration" in primary
    assert "[route]" in primary
    assert "Worker mode" in worker
    assert "[route]" not in worker
    assert "complexity" not in worker
    # Identity/context/skills are shared bytes.
    assert "I am Lattice" in worker
    assert "ctx" in worker
    assert "<skills/>" in worker


# --- run_turn routing ------------------------------------------------------


async def _run_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    inbound: Inbound,
    *,
    enabled: bool = True,
    route: RoutingDecision | None = None,
    worker: Any = None,
    model: Any = None,
) -> tuple[Any, list[str], dict[str, str]]:
    init_home(tmp_path)
    settings = LatticeSettings(home=tmp_path)
    settings.agent.workspace = tmp_path / "ws"
    settings.agent.orchestrator.enabled = enabled
    store = SessionStore(tmp_path / "state.db")
    # Keep the routing tests off the qdrant/fastembed native stack.
    from lattice.memory import InMemoryMemory

    monkeypatch.setattr(
        "lattice.turn.build_memory_for_profile", lambda *a, **k: InMemoryMemory("t")
    )

    calls: list[str] = []
    observed: dict[str, str] = {}

    async def fake_decide(
        _settings: LatticeSettings,
        model_id: str,
        system_prompt: str,
        user_text: str,
        *,
        session_id: str,
    ) -> tuple[RoutingDecision, RunUsage]:
        calls.append(user_text)
        observed["classifier_system"] = system_prompt
        decision = route or RoutingDecision(complexity=Complexity.HIGH, reason="test")
        return decision, RunUsage()

    monkeypatch.setattr("lattice.turn.decide_route", fake_decide)

    class _Result:
        def __init__(self, output: str) -> None:
            self.output = output
            self.usage = RunUsage()

    class _Agent:
        async def run(self, *args: Any, **kwargs: Any) -> _Result:
            return _Result("primary-ok")

    def fake_create_agent(*args: Any, **kwargs: Any) -> _Agent:
        observed["primary_system"] = kwargs.get("system_prompt", "")
        return _Agent()

    monkeypatch.setattr("lattice.turn.create_agent", fake_create_agent)
    if worker is not None:
        monkeypatch.setattr("lattice.turn.run_worker", worker)

    out = await run_turn(
        inbound,
        settings=settings,
        session_store=store,
        hitl=AutoApproveHitl(approve_all=True),
        model=model,
    )
    return out, calls, observed


@pytest.mark.asyncio
async def test_classifier_receives_user_text_and_high_runs_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, calls, observed = await _run_turn(
        monkeypatch, tmp_path, Inbound(text="explain the plan", profile_id="default")
    )
    assert calls == ["explain the plan"]
    assert out.text == "primary-ok"
    # Cache-sharing contract: classifier and primary get identical system bytes.
    assert observed["classifier_system"] == observed["primary_system"]
    assert "Orchestration" in observed["classifier_system"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inbound",
    [
        Inbound(text="x", media_paths=[Path("/tmp/lattice-routing.png")]),
        Inbound(text="x", steer_text="prefer bullets"),
    ],
)
async def test_bypass_media_and_steer_skip_classifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inbound: Inbound
) -> None:
    out, calls, _ = await _run_turn(monkeypatch, tmp_path, inbound)
    assert calls == []
    assert out.text == "primary-ok"


@pytest.mark.asyncio
async def test_bypass_injected_model_skips_classifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, calls, _ = await _run_turn(
        monkeypatch, tmp_path, Inbound(text="x", profile_id="default"), model=object()
    )
    assert calls == []
    assert out.text == "primary-ok"


@pytest.mark.asyncio
async def test_disabled_orchestrator_skips_classifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, calls, _ = await _run_turn(
        monkeypatch, tmp_path, Inbound(text="x", profile_id="default"), enabled=False
    )
    assert calls == []
    assert out.text == "primary-ok"


@pytest.mark.asyncio
async def test_low_dispatches_to_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_worker(deps: Any, *, task: str, system_prompt: str) -> tuple[str, RunUsage]:
        captured["task"] = task
        captured["system_prompt"] = system_prompt
        return "worker-answer", RunUsage()

    out, calls, _ = await _run_turn(
        monkeypatch,
        tmp_path,
        Inbound(text="rewrite this", profile_id="default"),
        route=RoutingDecision(complexity=Complexity.LOW, reason="simple"),
        worker=fake_worker,
    )
    assert calls == ["rewrite this"]
    assert out.text == "worker-answer"
    assert "rewrite this" in captured["task"]
    # Worker prompt is the user-facing variant without the classifier protocol.
    assert "Worker mode" in captured["system_prompt"]
    assert "[route]" not in captured["system_prompt"]


@pytest.mark.asyncio
async def test_worker_error_falls_back_to_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_worker(deps: Any, *, task: str, system_prompt: str) -> tuple[str, RunUsage]:
        raise RuntimeError("worker boom")

    out, _, _ = await _run_turn(
        monkeypatch,
        tmp_path,
        Inbound(text="x", profile_id="default"),
        route=RoutingDecision(complexity=Complexity.LOW, reason="simple"),
        worker=fake_worker,
    )
    assert out.text == "primary-ok"


@pytest.mark.asyncio
async def test_worker_empty_falls_back_to_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_worker(deps: Any, *, task: str, system_prompt: str) -> tuple[str, RunUsage]:
        return "   ", RunUsage()

    out, _, _ = await _run_turn(
        monkeypatch,
        tmp_path,
        Inbound(text="x", profile_id="default"),
        route=RoutingDecision(complexity=Complexity.LOW, reason="simple"),
        worker=fake_worker,
    )
    assert out.text == "primary-ok"
