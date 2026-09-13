"""Thin waist: run_turn(Inbound) -> Outbound."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from lattice.action_ledger import actions_from_messages, actions_from_tool_trace
from lattice.agent_app import (
    TurnDeps,
    build_memory_for_profile,
    build_prompt_bundle,
    build_search_description,
    create_agent,
    resolve_enabled_tools,
)
from lattice.channel.live_status import current_live_events
from lattice.config import LatticeSettings, load_settings
from lattice.context import PressureConfig, compress
from lattice.context.index import build_workspace_context
from lattice.context.pressure import resolve_context_window
from lattice.events import NullTurnEvents, TurnEvents
from lattice.hitl import AutoApproveHitl, HitlPort
from lattice.mcp import McpHostManager
from lattice.memory.worker import enqueue_sync
from lattice.models import Inbound, Outbound
from lattice.profiles import get_profile
from lattice.prompt import (
    build_action_notice,
    build_evidence_notice,
    build_runtime_context,
    build_runtime_notice,
)
from lattice.providers import (
    Summarizer,
    classify_provider_error,
    recovery_action,
)
from lattice.providers.caching import (
    prompt_cache_settings,
    session_routing_settings,
    supports_explicit_cache,
)
from lattice.providers.openai_compat import get_cached_openai_model
from lattice.providers.settings import resolve_model_id
from lattice.providers.usage import usage_to_dict
from lattice.runtime import set_cwd
from lattice.session import SessionStore, sanitize_messages
from lattice.session_history import session_dicts_to_history
from lattice.skills import scan_skills_for, skill_index_entries
from lattice.sqlite import SqlitePool, SqliteRegistry
from lattice.timeutil import resolve_timezone
from lattice.tools.deadline import with_deadline
from lattice.tools.todo import TodoList
from lattice.tools.user_tools import scan_user_tools
from lattice.turn_record import ContextRecord, TurnOutcome
from lattice.turn_trace import LoggingTurnEvents, new_turn_id

logger = logging.getLogger("lattice.turn")


class TurnCancelled(Exception):
    pass


# Files the agent produces during a turn that should reach the channel even when
# they were written by a script/shell rather than generate_chart/generate_pdf.
MEDIA_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".svg"})
_MEDIA_SKIP_DIRS = frozenset(
    {"inbound", ".git", "__pycache__", "node_modules", ".run", ".cache", ".venv"}
)
_MEDIA_MAX_FILES = 6
_MEDIA_MAX_BYTES = 25 * 1024 * 1024
# mtime can land a hair before turn_start on coarse filesystems.
_MEDIA_MTIME_SLACK = 1.0
# Tools that can create media; when none are enabled the per-turn media walks
# are skipped entirely (two full workspace walks saved).
_MEDIA_TOOLS = frozenset(
    {
        "shell",
        "execute_script",
        "write_file",
        "edit_file",
        "generate_chart",
        "generate_pdf",
        "browser_interact",
        "browser_snapshot",
        "remove_path",
    }
)


def snapshot_media(workspace: Path) -> set[Path]:
    """Absolute media paths present under the workspace before a turn runs."""
    found: set[Path] = set()
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _MEDIA_SKIP_DIRS]
        for name in files:
            if Path(name).suffix.lower() in MEDIA_EXTS:
                resolved = _safe_resolve(Path(root) / name)
                if resolved is not None:
                    found.add(resolved)
    return found


def _safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


def discover_turn_media(
    workspace: Path,
    *,
    turn_start: float,
    before: set[Path],
    existing: set[Path],
) -> list[Path]:
    """Workspace media created during the turn: deduped, bounded, oldest first."""
    candidates: list[tuple[float, Path]] = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _MEDIA_SKIP_DIRS]
        for name in files:
            path = Path(root) / name
            if path.suffix.lower() not in MEDIA_EXTS:
                continue
            resolved = _safe_resolve(path)
            if resolved is None or resolved in before or resolved in existing:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < turn_start - _MEDIA_MTIME_SLACK:
                continue
            if stat.st_size > _MEDIA_MAX_BYTES:
                continue
            candidates.append((stat.st_mtime, resolved))
    candidates.sort(key=lambda item: item[0])
    return [path for _, path in candidates[:_MEDIA_MAX_FILES]]


def _provider_error_text(exc: BaseException) -> str:
    """User-facing abort message; adds recovery for a rejected sticky model id."""
    text = str(exc)
    low = text.lower()
    if "not a valid model" in low or "model id" in low:
        return (
            f"The provider rejected the model: {text}\n\n"
            "If you set it with `/model`, run `/model clear` to fall back to the "
            "configured model."
        )
    return f"I hit a provider error: {text}"


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
    stream: bool = True,
    memory: Any | None = None,
) -> Outbound:
    settings = settings or load_settings()
    turn_id = new_turn_id()
    inner = events or current_live_events() or NullTurnEvents()

    # Media snapshot is deferred until the first media-capable tool actually
    # starts. Pure-text turns never pay the recursive workspace walk, and the
    # snapshot still captures pre-existing media before anything can write.
    media_before: set[Path] = set()
    media_capable = False
    media_snapshot_taken = False

    async def _on_tool_start(name: str) -> None:
        nonlocal media_snapshot_taken
        if media_snapshot_taken or not media_capable or name not in _MEDIA_TOOLS:
            return
        media_snapshot_taken = True
        media_before.update(await asyncio.to_thread(snapshot_media, workspace))

    trace = LoggingTurnEvents(
        turn_id,
        inner=inner,
        home=settings.home,
        record_enabled=settings.observability.turn_record,
        on_tool_start_hook=_on_tool_start,
    )
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
    todos = TodoList.from_items(existing.get("todos") if existing else None)
    context_record = ContextRecord(before_messages=len(messages))

    sticky_model = await store.get_sticky_primary_model(inbound.channel, inbound.user_id)
    primary_id = resolve_model_id(
        settings,
        profile_model=profile.primary_model or profile.model,
        sticky_model=sticky_model,
    )
    model_obj = model or get_cached_openai_model(settings, primary_id)
    summarizer_id = settings.agent.summarizer_model or primary_id

    notices: list[str] = []
    memory = memory or build_memory_for_profile(settings, profile, model_id=primary_id)

    async def _recall() -> list[dict[str, Any]]:
        timeout = float(settings.memory.search_timeout_seconds or 0.0)
        if timeout <= 0:
            return await memory.search(inbound.text, limit=5)
        try:
            return await asyncio.wait_for(memory.search(inbound.text, limit=5), timeout=timeout)
        except TimeoutError:
            logger.warning("memory search exceeded %.1fs; continuing without memories", timeout)
            return []

    registry = SqliteRegistry(settings, workspace=workspace)
    pool = SqlitePool(registry)
    # Overlap independent pre-model work: skill/tool discovery are disk scans,
    # workspace introspection is a directory listing + DB schema read, and memory
    # is an embedding round-trip. Run them off the loop instead of serially.
    with trace.timed("prefetch"):
        skill_report, user_tools, prefetch, workspace_context = await asyncio.gather(
            asyncio.to_thread(scan_skills_for, settings.home, profile),
            asyncio.to_thread(scan_user_tools, settings.home),
            _recall(),
            build_workspace_context(workspace, registry, pool, allow=profile.sqlite_allow),
        )
    skills = skill_report.skills
    for err in skill_report.errors:
        notices.append(f"[notice] {err}")
    entries = skill_index_entries(
        skills, prefer=profile.skills_prefer, disable=profile.skills_disable
    )
    for err in user_tools.errors:
        notices.append(f"[notice] {err}")
    user_specs = user_tools.specs
    if prefetch:
        notices.append("Relevant memories:\n" + "\n".join(f"- {h.get('text')}" for h in prefetch))

    # Telegram: inject UX skill body so formatting rules apply without relying on skill_view
    if inbound.channel == "telegram":
        from lattice.skills.activate import activate_skill

        tg_skill = activate_skill("telegram-chat", skills)
        if not tg_skill.startswith("skill not found"):
            notices.append(tg_skill)

    user_content = inbound.text
    if inbound.steer_text:
        user_content = f"{user_content}\n\n[steer] {inbound.steer_text}"
    if inbound.media_paths:
        paths = ", ".join(str(p) for p in inbound.media_paths)
        user_content = (
            f"{user_content}\n\n[media] {paths}\n(Use the ocr tool on image paths to extract text.)"
        )

    context_window = resolve_context_window(settings.agent.context_window_tokens, model_obj)
    pressure = PressureConfig(
        ratio=settings.agent.context_pressure_ratio,
        model_context_tokens=context_window,
    )
    # Include the pending user turn in the estimate: the stored transcript alone
    # under-counts and fires compression late. Calibrate against the last
    # request's real token count when available.
    prior_usage = (existing or {}).get("usage") or {}
    observed_tokens = int(prior_usage.get("last_input_tokens") or 0)
    observed_chars = int(prior_usage.get("last_context_chars") or 0)
    if pressure.is_over_pressure(
        messages,
        extra_chars=len(user_content),
        observed_tokens=observed_tokens,
        observed_chars=observed_chars,
    ):
        await events.on_status("compressing context")
        with trace.timed("compress"):
            enqueue_sync(
                memory,
                messages,
                turn_id=turn_id,
                timeout=float(settings.memory.sync_timeout_seconds),
            )
            aux = Summarizer(settings, summarizer_id)
            result = await compress(
                messages,
                aux=aux,
                protect_last_n=settings.agent.protect_last_n,
                pressure=pressure,
                force=True,
            )
        if result.compressed:
            notices.append("Context was compressed; older turns summarized.")
            context_record.compressed = True
            context_record.used_trim_fallback = result.used_trim_fallback
            parent_id = session_id
            session_id = await store.create(
                profile_id=profile.id,
                user_id=inbound.user_id,
                channel=inbound.channel,
                parent_id=parent_id,
            )
            messages = result.messages
            # The child session starts empty, so persist the compressed transcript
            # directly and carry the ledger/todos forward; otherwise the summary is
            # lost on the next turn.
            await store.save_messages(session_id, messages)
            if existing and existing.get("actions"):
                await store.append_actions(session_id, existing["actions"])
            if todos.items:
                await store.save_todos(session_id, todos.items)

    tz_name = resolve_timezone(settings.home, explicit=settings.timezone)
    try:
        now = datetime.now(ZoneInfo(tz_name)).isoformat(timespec="minutes")
    except Exception:
        now = datetime.now().astimezone().isoformat(timespec="minutes")
    runtime_context = build_runtime_context(
        workspace=str(workspace),
        timezone=tz_name,
        databases=[(d.name, str(d.path)) for d in registry.list()],
        profile_id=profile.id,
        preferred_skills=list(profile.skills_prefer),
        user_tools=[spec.name for spec in user_specs],
    )
    notices.append(build_runtime_notice(now=now, timezone=tz_name))
    action_notice = build_action_notice(existing.get("actions") if existing else None)
    if action_notice:
        notices.append(action_notice)
    if settings.agent.replay_evidence:
        evidence_notice = build_evidence_notice(
            existing.get("actions") if existing else None, limit=6, max_bytes=3000
        )
        if evidence_notice:
            notices.append(evidence_notice)
    if todos.items:
        notices.append("Todos:\n" + todos.render())
    # Cheap discovery facts so the model skips its own ls/find/schema warm-up;
    # built concurrently with the other prefetch work above.
    if workspace_context:
        notices.append(workspace_context)
    prompt = build_prompt_bundle(profile, entries, notices, runtime_context=runtime_context)
    system_prompt = prompt.stable_system_prompt()
    enabled = resolve_enabled_tools(
        settings,
        profile,
        channel=inbound.channel,
        mcp=mcp,
        extra_tools=[spec.name for spec in user_specs],
    )

    preamble = prompt.user_volatile_preamble()
    run_user_prompt = f"{preamble}\n\n{user_content}" if preamble else user_content

    cache_settings = cast(
        ModelSettings,
        {
            **prompt_cache_settings(settings, model_obj),
            **session_routing_settings(settings, session_id),
            "timeout": float(settings.agent.request_timeout_seconds),
        },
    )

    # History before this turn (cacheable prefix); current user saved separately
    history = session_dicts_to_history(
        messages,
        system_prompt=system_prompt,
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
        model=primary_id,
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
        turn_id=turn_id,
        todos=todos,
    )

    messages.append({"role": "user", "content": user_content})
    await store.append_message(session_id, {"role": "user", "content": user_content})

    agent = create_agent(
        settings,
        profile,
        system_prompt=system_prompt,
        model=model_obj,
        mcp=mcp,
        user_tools=user_specs,
        model_settings=cache_settings,
        search_description=build_search_description(enabled, settings, mcp, user_specs),
    )

    async def _on_stream(_ctx: Any, stream_events: Any) -> None:
        async for event in stream_events:
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                if event.part.content:
                    await events.on_stream_delta(event.part.content)
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                delta = event.delta.content_delta
                if delta:
                    await events.on_stream_delta(delta)

    async def _run_once() -> tuple[str, RunUsage, list[Any]]:
        result = await agent.run(
            run_user_prompt,
            deps=deps,
            message_history=history or None,
            usage_limits=UsageLimits(request_limit=settings.agent.iteration_budget),
            event_stream_handler=_on_stream if stream else None,
        )
        return str(result.output), result.usage, result.new_messages()

    deadline = float(settings.agent.turn_timeout_seconds)
    text = ""
    err: str | None = None
    outcome = TurnOutcome.COMPLETED
    error_kind: str | None = None
    usage = RunUsage()
    retries = 0
    run_messages: list[Any] = []

    def _usage_payload() -> dict[str, Any]:
        data = usage_to_dict(usage, model=primary_id)
        # Persist the live context size so the next turn can calibrate pressure.
        # Tokens and chars must describe the *same* real request; the persisted
        # transcript drops tool results, so read the serialized request length the
        # logging wrapper captured instead of re-estimating from ``messages``.
        model_ref = getattr(agent, "model", None)
        last_tokens = getattr(model_ref, "last_input_tokens", 0)
        last_chars = int(getattr(model_ref, "last_request_chars", 0) or 0)
        data["last_input_tokens"] = int(last_tokens or 0)
        if last_chars <= 0:
            # Non-logging model (e.g. tests): best-effort transcript estimate.
            last_chars = pressure.estimate_chars(messages)
        data["last_context_chars"] = last_chars
        return data

    usage_payload = _usage_payload()
    media_capable = bool(_MEDIA_TOOLS.intersection(enabled))
    turn_start = time.time()

    def _rebuild_history() -> None:
        nonlocal history
        history = session_dicts_to_history(
            messages,
            system_prompt=system_prompt,
            cache_boundary=settings.agent.prompt_cache and supports_explicit_cache(model_obj),
            cache_ttl=settings.agent.prompt_cache_ttl,
        )

    async def _run_with_cancel() -> tuple[str, RunUsage, list[Any]]:
        """Run one attempt, racing it against the total deadline and cancel event."""
        run_task = asyncio.ensure_future(_run_once())
        if cancel_event is None:
            return await with_deadline(run_task, seconds=deadline, label="turn")
        cancel_task = asyncio.ensure_future(cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                {run_task, cancel_task},
                timeout=deadline,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                run_task.cancel()
                with contextlib.suppress(BaseException):
                    await run_task
                raise TimeoutError(f"turn exceeded {deadline:.0f}s")
            if cancel_task in done and run_task not in done:
                run_task.cancel()
                with contextlib.suppress(BaseException):
                    await run_task
                raise TurnCancelled("cancelled")
            cancel_task.cancel()
            with contextlib.suppress(BaseException):
                await cancel_task
            return await run_task
        finally:
            for task in (run_task, cancel_task):
                if not task.done():
                    task.cancel()

    try:
        with trace.timed("executor"):
            await events.on_status("thinking")
            while True:
                if cancel_event and cancel_event.is_set():
                    outcome = TurnOutcome.CANCELLED
                    text = text or "[cancelled]"
                    break
                try:
                    text, attempt_usage, attempt_messages = await _run_with_cancel()
                    usage = usage + attempt_usage
                    run_messages = attempt_messages
                    if not text.strip():
                        # An empty completion after tools ran is not retryable:
                        # re-running would replay side effects.
                        if trace.tool_calls:
                            outcome = TurnOutcome.EMPTY
                            text = "(no reply)"
                            break
                        raise RuntimeError("empty completion")
                    outcome = TurnOutcome.COMPLETED
                    break
                except TurnCancelled:
                    outcome = TurnOutcome.CANCELLED
                    text = text or "[cancelled]"
                    break
                except asyncio.CancelledError:
                    outcome = TurnOutcome.CANCELLED
                    text = text or "[cancelled]"
                    break
                except TimeoutError as exc:
                    outcome = TurnOutcome.TIMEOUT
                    err = str(exc)
                    text = (
                        f"This turn hit its {deadline:.0f}s deadline before finishing. "
                        "Ask me to continue and I'll pick up from here."
                    )
                    break
                except Exception as exc:
                    if isinstance(exc, UsageLimitExceeded):
                        outcome = TurnOutcome.BUDGET
                        text = (
                            f"I reached this turn's request budget "
                            f"({settings.agent.iteration_budget} model requests) "
                            "before finishing. Ask me to continue and I'll pick up "
                            "from here."
                        )
                        err = str(exc)
                        break
                    reason = classify_provider_error(exc)
                    action = recovery_action(reason)
                    error_kind = reason.value
                    await events.on_status(f"provider {reason.value} → {action}")
                    if action == "retry" and retries < 2:
                        if trace.tool_calls:
                            outcome = TurnOutcome.ERROR
                            err = str(exc)
                            text = (
                                "I hit a transient provider error after running tools. "
                                "To avoid repeating side effects I stopped here — ask me "
                                "to continue and I'll pick up from where I left off."
                            )
                            break
                        retries += 1
                        continue
                    if action == "compress":
                        if trace.tool_calls:
                            # Tools already ran this attempt; replaying the turn
                            # after a compress would repeat their side effects.
                            outcome = TurnOutcome.ERROR
                            err = str(exc)
                            text = (
                                "I hit a context-overflow after running tools. "
                                "To avoid repeating side effects I stopped here — ask me "
                                "to continue and I'll pick up from where I left off."
                            )
                            break
                        enqueue_sync(
                            memory,
                            messages,
                            turn_id=turn_id,
                            timeout=float(settings.memory.sync_timeout_seconds),
                        )
                        aux = Summarizer(settings, summarizer_id)
                        result = await compress(
                            messages,
                            aux=aux,
                            protect_last_n=settings.agent.protect_last_n,
                            pressure=pressure,
                            force=True,
                        )
                        if result.compressed:
                            context_record.compressed = True
                            context_record.used_trim_fallback = result.used_trim_fallback
                            messages = result.messages
                            parent_id = session_id
                            session_id = await store.create(
                                profile_id=profile.id,
                                user_id=inbound.user_id,
                                channel=inbound.channel,
                                parent_id=parent_id,
                            )
                            deps.session_id = session_id
                            _rebuild_history()
                            await store.save_messages(session_id, messages)
                            if existing and existing.get("actions"):
                                await store.append_actions(session_id, existing["actions"])
                            if todos.items:
                                await store.save_todos(session_id, todos.items)
                        retries += 1
                        if retries < 3:
                            continue
                    outcome = (
                        TurnOutcome.EMPTY if error_kind == "empty_completion" else TurnOutcome.ERROR
                    )
                    if action == "abort" or retries >= 3:
                        text = _provider_error_text(exc)
                        err = str(exc)
                        break
                    text = _provider_error_text(exc)
                    err = str(exc)
                    break
    except TurnCancelled:
        outcome = TurnOutcome.CANCELLED
        text = text or "[cancelled]"
    except asyncio.CancelledError:
        # An outer task cancel (e.g. Telegram /stop) that lands outside the retry
        # loop must still persist an assistant tombstone.
        outcome = TurnOutcome.CANCELLED
        text = text or "[cancelled]"
    finally:
        if text:
            messages.append({"role": "assistant", "content": text})
            usage_payload = _usage_payload()
            with contextlib.suppress(Exception):
                await store.append_message(
                    session_id, {"role": "assistant", "content": text}, usage=usage_payload
                )
            enqueue_sync(
                memory,
                messages[-4:],
                turn_id=turn_id,
                timeout=float(settings.memory.sync_timeout_seconds),
            )
        context_record.after_messages = len(messages)
        await pool.close_all()
        actions = actions_from_messages(run_messages, media=list(deps.outbound_media))
        if not actions and trace.tools:
            # The run never returned a message list (timeout/cancel/budget/provider
            # error) but tools already executed: persist their side effects.
            actions = actions_from_tool_trace(trace.tools, media=list(deps.outbound_media))
        if actions:
            with contextlib.suppress(Exception):
                await store.append_actions(session_id, actions)
        # Todos are best-effort state: never let persistence fail a turn.
        with contextlib.suppress(Exception):
            await store.save_todos(session_id, deps.todos.items)
        trace.log_end(
            outbound_text=text or "",
            error=err,
            usage=usage_payload,
            timings=trace.phases,
            outcome=outcome,
            retries=retries,
            context=context_record,
            error_kind=error_kind,
        )

    media = [p for p in deps.outbound_media if p.exists()]
    # Only walk the workspace when a media-capable tool actually executed; most
    # turns enable shell/write_file but never write anything.
    ran_media_tool = any(t.name in _MEDIA_TOOLS for t in trace.tools)
    if media_capable and ran_media_tool:
        existing_media = {p.resolve() for p in media}
        media.extend(
            discover_turn_media(
                workspace,
                turn_start=turn_start,
                before=media_before,
                existing=existing_media,
            )
        )
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
