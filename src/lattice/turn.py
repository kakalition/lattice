"""Thin waist: run_turn(Inbound) -> Outbound."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

from lattice.agent_app import (
    TurnDeps,
    build_memory_for_profile,
    build_prompt_bundle,
    create_agent,
    resolve_enabled_tools,
)
from lattice.agents.secondary import run_worker
from lattice.channel.live_status import current_live_events
from lattice.config import LatticeSettings, load_settings
from lattice.context import PressureConfig, compress
from lattice.events import NullTurnEvents, TurnEvents
from lattice.hitl import AutoApproveHitl, HitlPort
from lattice.mcp import McpHostManager
from lattice.models import Inbound, Outbound
from lattice.orchestrator import (
    CLASSIFIER_FALLBACK_REASONS,
    Complexity,
    decide_route,
    heuristic_route,
)
from lattice.profiles import get_profile
from lattice.providers import (
    AuxiliaryClient,
    build_openai_model,
    classify_provider_error,
    recovery_action,
)
from lattice.providers.caching import (
    prompt_cache_settings,
    session_routing_settings,
    supports_explicit_cache,
)
from lattice.providers.fallback_cooldown import FallbackCooldown
from lattice.providers.settings import (
    classifier_model_name,
    resolve_model_id,
    secondary_model_name,
)
from lattice.providers.usage import usage_to_dict
from lattice.runtime import set_cwd
from lattice.session import SessionStore, sanitize_messages
from lattice.session_history import session_dicts_to_history
from lattice.skills import scan_skills_for, skill_index_entries
from lattice.sqlite import SqlitePool, SqliteRegistry
from lattice.tools.deadline import with_deadline
from lattice.tools.user_tools import scan_user_tools
from lattice.turn_trace import LoggingTurnEvents, new_turn_id


class TurnCancelled(Exception):
    pass


async def _cancel_worker(task: asyncio.Task[tuple[str, RunUsage]] | None) -> None:
    """Cancel a speculative worker and fully await it (never overlap the primary)."""
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _settled_usage(task: asyncio.Task[tuple[str, RunUsage]]) -> RunUsage:
    """Usage from a settled speculative task; empty when cancelled or failed."""
    if task.cancelled() or task.exception() is not None:
        return RunUsage()
    return task.result()[1]


async def run_turn(
    inbound: Inbound,
    *,
    settings: LatticeSettings | None = None,
    hitl: HitlPort | None = None,
    events: TurnEvents | None = None,
    session_store: SessionStore | None = None,
    mcp: McpHostManager | None = None,
    cancel_event: asyncio.Event | None = None,
    model: Any | None = None,
) -> Outbound:
    settings = settings or load_settings()
    turn_id = new_turn_id()
    inner = events or current_live_events() or NullTurnEvents()
    trace = LoggingTurnEvents(turn_id, inner=inner)
    events = trace
    hitl = hitl or AutoApproveHitl(approve_all=False)
    store = session_store or SessionStore(settings.home / "state.db")
    mcp = mcp or McpHostManager()
    profile = get_profile(inbound.profile_id, settings.home)

    if inbound.cancel:
        raise TurnCancelled("cancel requested")

    session_id = inbound.session_id
    if not session_id:
        session_id = await store.create(
            profile_id=profile.id,
            user_id=inbound.user_id,
            channel=inbound.channel,
        )

    workspace = (
        profile.workspace
        or settings.agent.workspace
        or (Path.cwd() if inbound.channel == "cli" else settings.home / "workspace")
    )
    if isinstance(workspace, str):
        workspace = Path(workspace)
    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    set_cwd(workspace)

    await events.on_status("loading session")
    existing = await store.get(session_id)
    messages: list[dict[str, Any]] = sanitize_messages(existing["messages"] if existing else [])
    # Prior route is persisted with usage; feeds the sticky-HIGH heuristic.
    prior_route = (existing or {}).get("usage", {}).get("route")
    history_len = len(messages)

    notices: list[str] = []
    skill_report = scan_skills_for(settings.home, profile)
    skills = skill_report.skills
    for err in skill_report.errors:
        notices.append(f"[notice] {err}")
    entries = skill_index_entries(
        skills, prefer=profile.skills_prefer, disable=profile.skills_disable
    )
    user_tools = scan_user_tools(settings.home)
    for err in user_tools.errors:
        notices.append(f"[notice] {err}")
    user_specs = user_tools.specs

    memory = build_memory_for_profile(settings, profile)
    prefetch = await memory.search(inbound.text, limit=5)
    if prefetch:
        notices.append("Relevant memories:\n" + "\n".join(f"- {h.get('text')}" for h in prefetch))

    # Telegram: inject UX skill body so formatting rules apply without relying on skill_view
    if inbound.channel == "telegram":
        from lattice.skills.activate import activate_skill

        tg_skill = activate_skill("telegram-chat", skills)
        if not tg_skill.startswith("skill not found"):
            notices.append(tg_skill)

    pressure = PressureConfig(ratio=settings.agent.context_pressure_ratio)
    if pressure.is_over_pressure(messages):
        await events.on_status("compressing context")
        await memory.sync_turn(messages)
        aux = AuxiliaryClient(settings, profile.auxiliary_model)
        result = await compress(
            messages,
            aux=aux,
            protect_last_n=settings.agent.protect_last_n,
            pressure=pressure,
        )
        if result.compressed:
            notices.append("Context was compressed; older turns summarized.")
            parent_id = session_id
            session_id = await store.create(
                profile_id=profile.id,
                user_id=inbound.user_id,
                channel=inbound.channel,
                parent_id=parent_id,
            )
            messages = result.messages

    prompt = build_prompt_bundle(profile, entries, notices)
    system_prompt = prompt.stable_system_prompt()

    registry = SqliteRegistry(settings)
    pool = SqlitePool(registry)
    enabled = resolve_enabled_tools(
        settings,
        profile,
        channel=inbound.channel,
        mcp=mcp,
        extra_tools=[spec.name for spec in user_specs],
    )

    user_content = inbound.text
    if inbound.steer_text:
        user_content = f"{user_content}\n\n[steer] {inbound.steer_text}"
    if inbound.media_paths:
        paths = ", ".join(str(p) for p in inbound.media_paths)
        user_content = (
            f"{user_content}\n\n[media] {paths}\n(Use the ocr tool on image paths to extract text.)"
        )

    preamble = prompt.user_volatile_preamble()
    run_user_prompt = f"{preamble}\n\n{user_content}" if preamble else user_content

    sticky_model = await store.get_sticky_primary_model(inbound.channel, inbound.user_id)
    primary_id = resolve_model_id(
        settings,
        profile_model=profile.primary_model or profile.model,
        sticky_model=sticky_model,
    )
    model_obj = model or build_openai_model(settings, primary_id)
    cache_settings = cast(
        ModelSettings,
        {
            **prompt_cache_settings(settings, model_obj),
            **session_routing_settings(settings, session_id),
        },
    )
    worker_id = secondary_model_name(settings, profile_secondary=profile.secondary_model)
    classifier_id = classifier_model_name(
        settings,
        profile_model=profile.primary_model or profile.model,
        sticky_model=sticky_model,
    )

    # History before this turn (cacheable prefix); current user saved separately
    history = session_dicts_to_history(
        messages,
        cache_boundary=settings.agent.prompt_cache and supports_explicit_cache(model_obj),
        cache_ttl=settings.agent.prompt_cache_ttl,
    )

    trace.log_begin(
        channel=inbound.channel,
        user_id=inbound.user_id,
        profile_id=profile.id,
        session_id=session_id,
        inbound_text=user_content,
        tools=enabled,
        skills=entries,
    )

    deps = TurnDeps(
        settings=settings,
        profile=profile,
        hitl=hitl,
        session=store,
        session_id=session_id,
        memory=memory,
        sqlite_registry=registry,
        sqlite_pool=pool,
        mcp=mcp,
        events=events,
        workspace=workspace,
        enabled_tools=enabled,
        skills=skills,
        user_tools=user_specs,
        user_id=inbound.user_id,
        channel=inbound.channel,
        cooldown=FallbackCooldown(),
    )

    messages.append({"role": "user", "content": user_content})
    await store.save_messages(session_id, messages)

    agent = create_agent(
        settings,
        profile,
        system_prompt=system_prompt,
        model=model_obj,
        mcp=mcp,
        user_tools=user_specs,
        model_settings=cache_settings,
    )

    async def _run_once(model_override: str | None = None) -> tuple[str, RunUsage]:
        kwargs: dict[str, Any] = {
            "deps": deps,
            "message_history": history or None,
        }
        if model is not None and model_override is None:
            result = await agent.run(run_user_prompt, **kwargs)
            return str(result.output), result.usage
        if model_override:
            from copy import deepcopy

            s2 = deepcopy(settings)
            s2.agent.primary_model = model_override
            override_model = build_openai_model(s2, model_override)
            local_agent = create_agent(
                s2,
                profile,
                system_prompt=system_prompt,
                model=override_model,
                mcp=mcp,
                user_tools=user_specs,
                model_settings=cast(
                    ModelSettings,
                    {
                        **prompt_cache_settings(s2, override_model),
                        **session_routing_settings(s2, session_id),
                    },
                ),
            )
            result = await local_agent.run(run_user_prompt, **kwargs)
            return str(result.output), result.usage
        result = await agent.run(run_user_prompt, **kwargs)
        return str(result.output), result.usage

    orch = settings.agent.orchestrator
    deadline = float(settings.agent.idle_watchdog_seconds)
    worker_deadline = float(orch.worker_timeout_seconds)
    text = ""
    err: str | None = None
    route = Complexity.HIGH
    route_source = "bypass"
    classifier_usage = RunUsage()
    wasted_worker_usage = RunUsage()
    executor_id = primary_id
    retries = 0
    pending_worker: asyncio.Task[tuple[str, RunUsage]] | None = None

    # Deterministic HIGH bypass: media/steer/injected model must reach the primary
    # without an extra classifier request (and get the full multi-step loop).
    routing_enabled = (
        orch.enabled and model is None and not inbound.media_paths and not inbound.steer_text
    )
    if routing_enabled:
        with trace.timed("routing"):
            await events.on_status("routing")
            gated = (
                heuristic_route(
                    user_content,
                    prior_route=prior_route,
                    history_len=history_len,
                    sticky_high=orch.sticky_high,
                )
                if orch.heuristic_gate
                else None
            )
            if gated is not None:
                route = gated
                route_source = "heuristic"
                await events.on_status(f"route={route.value.lower()} (heuristic)")
            else:
                # Classify and (optionally) speculate the LOW worker together so an
                # ambiguous LOW pays max(classifier, worker) instead of the sum.
                classify_task = asyncio.ensure_future(
                    decide_route(
                        settings,
                        classifier_id,
                        system_prompt,
                        user_content,
                        session_id=session_id,
                    )
                )
                if orch.speculative_worker:
                    pending_worker = asyncio.ensure_future(
                        with_deadline(
                            run_worker(
                                deps,
                                task=run_user_prompt,
                                system_prompt=prompt.worker_system_prompt(),
                            ),
                            seconds=worker_deadline,
                            label="worker",
                        )
                    )
                try:
                    with trace.timed("classifier"):
                        decision, classifier_usage = await classify_task
                except BaseException:
                    await _cancel_worker(pending_worker)
                    pending_worker = None
                    raise
                route = decision.complexity
                route_source = (
                    "fallback" if decision.reason in CLASSIFIER_FALLBACK_REASONS else "classifier"
                )
                await events.on_status(f"route={route.value.lower()} ({decision.reason})")
                if route is Complexity.HIGH and pending_worker is not None:
                    # Shared deps/HITL/cwd: settle the worker before the primary starts.
                    await _cancel_worker(pending_worker)
                    wasted_worker_usage = _settled_usage(pending_worker)
                    pending_worker = None

    usage = classifier_usage

    def _usage_payload() -> dict[str, Any]:
        payload = usage_to_dict(usage, model=executor_id)
        payload["route"] = route.value.lower()
        payload["route_source"] = route_source
        payload["classifier"] = usage_to_dict(classifier_usage, model=classifier_id)
        payload["wasted_worker_usage"] = usage_to_dict(wasted_worker_usage, model=worker_id)
        return payload

    usage_payload = _usage_payload()
    try:
        with trace.timed("executor"):
            if route is Complexity.LOW:
                if cancel_event and cancel_event.is_set():
                    raise TurnCancelled("cancelled")
                try:
                    if pending_worker is not None:
                        worker_text, worker_usage = await pending_worker
                        pending_worker = None
                    else:
                        worker_text, worker_usage = await with_deadline(
                            run_worker(
                                deps,
                                task=run_user_prompt,
                                system_prompt=prompt.worker_system_prompt(),
                            ),
                            seconds=worker_deadline,
                            label="worker",
                        )
                except TurnCancelled:
                    raise
                except Exception as exc:
                    await events.on_status(f"worker failed → high ({exc})")
                    route = Complexity.HIGH
                else:
                    if worker_text.strip():
                        text = worker_text
                        usage = usage + worker_usage
                        executor_id = worker_id
                    else:
                        await events.on_status("worker empty → high")
                        route = Complexity.HIGH

            if route is Complexity.HIGH:
                await events.on_status("thinking")
                while True:
                    if cancel_event and cancel_event.is_set():
                        raise TurnCancelled("cancelled")
                    try:
                        text, attempt_usage = await with_deadline(
                            _run_once(), seconds=deadline, label="turn"
                        )
                        usage = usage + attempt_usage
                        if not text.strip():
                            raise RuntimeError("empty completion")
                        break
                    except TurnCancelled:
                        raise
                    except Exception as exc:
                        reason = classify_provider_error(exc)
                        action = recovery_action(reason)
                        await events.on_status(f"provider {reason.value} → {action}")
                        if action == "retry" and retries < 2:
                            retries += 1
                            continue
                        if action == "compress":
                            await memory.sync_turn(messages)
                            aux = AuxiliaryClient(settings, profile.auxiliary_model)
                            result = await compress(
                                messages,
                                aux=aux,
                                protect_last_n=settings.agent.protect_last_n,
                                pressure=pressure,
                            )
                            messages = result.messages
                            await store.save_messages(session_id, messages)
                            retries += 1
                            if retries < 3:
                                continue
                        if action == "fallback" and settings.provider.fallback_model:
                            deps.cooldown.mark_fallback()
                            try:
                                text, attempt_usage = await with_deadline(
                                    _run_once(settings.provider.fallback_model),
                                    seconds=deadline,
                                    label="fallback turn",
                                )
                                usage = usage + attempt_usage
                                break
                            except Exception:
                                text = f"I hit a provider error: {exc}"
                                err = str(exc)
                                break
                        if action == "abort" or retries >= 3:
                            text = f"I hit a provider error: {exc}"
                            err = str(exc)
                            break
                        text = f"I hit a provider error: {exc}"
                        err = str(exc)
                        break
    finally:
        # An early abort can leave the speculative worker pending; never orphan it.
        await _cancel_worker(pending_worker)
        pending_worker = None
        if text:
            messages.append({"role": "assistant", "content": text})
            usage_payload = _usage_payload()
            await store.save_messages(session_id, messages, usage=usage_payload)
            await memory.sync_turn(messages[-4:])
        await pool.close_all()
        trace.log_end(
            outbound_text=text or "",
            error=err,
            usage=usage_payload,
            route=route.value.lower(),
            route_source=route_source,
            timings=trace.phases,
        )

    await events.on_stream_delta(text)
    media = [p for p in deps.outbound_media if p.exists()]
    return Outbound(
        text=text,
        session_id=session_id,
        profile_id=profile.id,
        media_paths=media or None,
    )


async def echo_turn(inbound: Inbound) -> Outbound:
    """Offline smoke path when no API key is configured."""
    return Outbound(
        text=f"[{inbound.profile_id}] {inbound.text}",
        session_id=inbound.session_id or "echo",
        profile_id=inbound.profile_id,
    )
