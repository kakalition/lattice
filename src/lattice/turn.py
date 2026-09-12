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
from lattice.channel.live_status import current_live_events
from lattice.config import LatticeSettings, load_settings
from lattice.context import PressureConfig, compress
from lattice.events import NullTurnEvents, TurnEvents
from lattice.hitl import AutoApproveHitl, HitlPort
from lattice.mcp import McpHostManager
from lattice.models import Inbound, Outbound
from lattice.profiles import get_profile
from lattice.providers import (
    Summarizer,
    build_openai_model,
    classify_provider_error,
    recovery_action,
)
from lattice.providers.caching import (
    prompt_cache_settings,
    session_routing_settings,
    supports_explicit_cache,
)
from lattice.providers.settings import resolve_model_id
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

    sticky_model = await store.get_sticky_primary_model(inbound.channel, inbound.user_id)
    primary_id = resolve_model_id(
        settings,
        profile_model=profile.primary_model or profile.model,
        sticky_model=sticky_model,
    )
    model_obj = model or build_openai_model(settings, primary_id)

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

    memory = build_memory_for_profile(settings, profile, model_id=primary_id)
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
        aux = Summarizer(settings, primary_id)
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

    cache_settings = cast(
        ModelSettings,
        {
            **prompt_cache_settings(settings, model_obj),
            **session_routing_settings(settings, session_id),
        },
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

    async def _run_once() -> tuple[str, RunUsage]:
        result = await agent.run(
            run_user_prompt,
            deps=deps,
            message_history=history or None,
        )
        return str(result.output), result.usage

    deadline = float(settings.agent.idle_watchdog_seconds)
    text = ""
    err: str | None = None
    usage = RunUsage()
    retries = 0

    def _usage_payload() -> dict[str, Any]:
        return usage_to_dict(usage, model=primary_id)

    usage_payload = _usage_payload()
    try:
        with trace.timed("executor"):
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
                        aux = Summarizer(settings, primary_id)
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
                    if action == "abort" or retries >= 3:
                        text = f"I hit a provider error: {exc}"
                        err = str(exc)
                        break
                    text = f"I hit a provider error: {exc}"
                    err = str(exc)
                    break
    finally:
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
