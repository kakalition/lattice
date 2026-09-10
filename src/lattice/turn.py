"""Thin waist: run_turn(Inbound) -> Outbound."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from lattice.agent_app import (
    TurnDeps,
    build_memory_for_profile,
    build_prompt_bundle,
    create_agent,
    resolve_enabled_tools,
)
from lattice.config import LatticeSettings, load_settings
from lattice.context import PressureConfig, compress
from lattice.events import NullTurnEvents, TurnEvents
from lattice.hitl import AutoApproveHitl, HitlPort
from lattice.mcp import McpHostManager
from lattice.models import Inbound, Outbound
from lattice.profiles import get_profile
from lattice.providers import (
    AuxiliaryClient,
    classify_provider_error,
    recovery_action,
)
from lattice.providers.fallback_cooldown import FallbackCooldown
from lattice.runtime import set_cwd
from lattice.session import SessionStore, sanitize_messages
from lattice.skills import scan_skills, skill_index_entries
from lattice.sqlite import SqlitePool, SqliteRegistry
from lattice.tools.deadline import with_deadline


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
) -> Outbound:
    settings = settings or load_settings()
    events = events or NullTurnEvents()
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

    extra_dirs = []
    if profile.root and (profile.root / "skills").is_dir():
        extra_dirs.append(profile.root / "skills")
    skills = scan_skills(settings.home, extra_dirs=extra_dirs)
    entries = skill_index_entries(
        skills, prefer=profile.skills_prefer, disable=profile.skills_disable
    )

    memory = build_memory_for_profile(settings, profile)
    prefetch = await memory.search(inbound.text, limit=5)
    notices: list[str] = []
    if prefetch:
        notices.append("Relevant memories:\n" + "\n".join(f"- {h.get('text')}" for h in prefetch))

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
            # lineage: create child session
            parent_id = session_id
            session_id = await store.create(
                profile_id=profile.id,
                user_id=inbound.user_id,
                channel=inbound.channel,
                parent_id=parent_id,
            )
            messages = result.messages

    prompt = build_prompt_bundle(profile, entries, notices)
    system_prompt = prompt.system_prompt()

    registry = SqliteRegistry(settings)
    pool = SqlitePool(registry)
    enabled = resolve_enabled_tools(settings, profile, channel=inbound.channel, mcp=mcp)

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
        user_id=inbound.user_id,
        channel=inbound.channel,
        cooldown=FallbackCooldown(),
    )

    user_content = inbound.text
    if inbound.steer_text:
        user_content = f"{user_content}\n\n[steer] {inbound.steer_text}"
    if inbound.media_paths:
        paths = ", ".join(str(p) for p in inbound.media_paths)
        user_content = f"{user_content}\n\n[media] {paths}"

    # Persist user message before model call (persist-before-execute pattern for turns)
    messages.append({"role": "user", "content": user_content})
    await store.save_messages(session_id, messages)

    agent = create_agent(settings, profile, system_prompt=system_prompt)

    async def _run_once(model_override: str | None = None) -> str:
        if model_override:
            from copy import deepcopy

            s2 = deepcopy(settings)
            s2.agent.model = model_override
            local_agent = create_agent(s2, profile, system_prompt=system_prompt)
            result = await local_agent.run(user_content, deps=deps)
            return str(result.output)
        result = await agent.run(user_content, deps=deps)
        return str(result.output)

    await events.on_status("thinking")
    deadline = float(settings.agent.idle_watchdog_seconds)
    text = ""
    usage: dict[str, Any] = {}
    retries = 0
    while True:
        if cancel_event and cancel_event.is_set():
            raise TurnCancelled("cancelled")
        try:
            text = await with_deadline(_run_once(), seconds=deadline, label="turn")
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
                    text = await with_deadline(
                        _run_once(settings.provider.fallback_model),
                        seconds=deadline,
                        label="fallback turn",
                    )
                    break
                except Exception:
                    text = f"I hit a provider error: {exc}"
                    break
            if action == "abort" or retries >= 3:
                text = f"I hit a provider error: {exc}"
                break
            text = f"I hit a provider error: {exc}"
            break

    messages.append({"role": "assistant", "content": text})
    usage = {"model": profile.model or settings.agent.model}
    await store.save_messages(session_id, messages, usage=usage)
    await memory.sync_turn(messages[-4:])
    await pool.close_all()

    await events.on_stream_delta(text)
    return Outbound(text=text, session_id=session_id, profile_id=profile.id)


async def echo_turn(inbound: Inbound) -> Outbound:
    """Offline smoke path when no API key is configured."""
    return Outbound(
        text=f"[{inbound.profile_id}] {inbound.text}",
        session_id=inbound.session_id or "echo",
        profile_id=inbound.profile_id,
    )
