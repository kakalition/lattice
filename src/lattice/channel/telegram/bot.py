"""Telegram bot runner — DM-only native UX."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from lattice.branding import brand_label, logo_path
from lattice.channel.live_status import (
    LiveTurnEvents,
    bind_live_events,
    idle_phrase,
    typing_keepalive,
    warm_confirmation,
)
from lattice.channel.telegram.commands import COMMANDS
from lattice.channel.telegram.formatting import chunk_text, markdown_to_telegram_html
from lattice.channel.telegram.keyboards import inline_keyboard
from lattice.config import LatticeSettings
from lattice.hitl.telegram_adapter import TelegramHitlAdapter
from lattice.models import Inbound, Outbound
from lattice.profiles import list_profiles, remove_profile
from lattice.session import SessionStore

logger = logging.getLogger("lattice.channel.telegram")


class TelegramBot:
    def __init__(
        self,
        settings: LatticeSettings,
        handler: Callable[[Inbound], Awaitable[Outbound]],
        *,
        hitl: TelegramHitlAdapter | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self.settings = settings
        self.handler = handler
        self.hitl = hitl or TelegramHitlAdapter(timeout_seconds=settings.agent.hitl_timeout_seconds)
        self.store = store or SessionStore(settings.home / "state.db")
        self._sessions: dict[int, str] = {}
        self._busy: set[int] = set()
        self._queues: dict[int, asyncio.Queue[str]] = {}
        self._seen_updates: set[int] = set()
        self._cancel: dict[int, asyncio.Event] = {}
        self._turns: dict[int, asyncio.Task[Any]] = {}

    def name(self) -> str:
        return "telegram"

    async def send(self, msg: Outbound) -> None:
        # Bound at runtime via bot context
        return None

    async def run(self, handler: Callable[[Inbound], Awaitable[Outbound]] | None = None) -> None:
        if handler:
            self.handler = handler
        token = self.settings.telegram.token
        if not token:
            raise RuntimeError("telegram.token not configured")

        from telegram import BotCommand, Update
        from telegram.error import RetryAfter
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )

        app = (
            Application.builder()
            .token(token)
            # HITL Approve/Deny callbacks must run while MessageHandler awaits hitl.approve().
            .concurrent_updates(True)
            .build()
        )

        async def _post_init(application: Any) -> None:
            await application.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])

        app.post_init = _post_init

        async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            logger.warning("telegram update failed: %s", context.error)

        app.add_error_handler(_on_error)

        def _retry_seconds(exc: RetryAfter) -> float:
            retry_after = exc.retry_after
            if hasattr(retry_after, "total_seconds"):
                return float(retry_after.total_seconds())
            return float(retry_after)

        async def _flood_safe(call: Callable[[], Awaitable[Any]]) -> Any:
            """Run a bot call, sleeping through Telegram flood control (up to twice)."""
            for attempt in range(3):
                try:
                    return await call()
                except RetryAfter as exc:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(_retry_seconds(exc) + 0.5)

        async def _reply(update: Update, text: str, **kwargs: Any) -> Any:
            message = update.effective_message
            if message is None:
                return None
            return await _flood_safe(
                lambda text=text, kwargs=kwargs: message.reply_text(text, **kwargs)
            )

        async def _edit(query: Any, text: str, **kwargs: Any) -> Any:
            return await _flood_safe(
                lambda text=text, kwargs=kwargs: query.edit_message_text(text, **kwargs)
            )

        async def _allowed(user_id: int) -> bool:
            allow = self.settings.telegram.allowlist
            return not allow or user_id in allow

        async def send_html(
            chat_id: int,
            text: str,
            *,
            buttons: list[dict[str, str]] | None = None,
            edit_message_id: int | None = None,
            context: ContextTypes.DEFAULT_TYPE,
        ) -> None:
            markup = inline_keyboard(buttons) if buttons else None
            html_chunks = chunk_text(markdown_to_telegram_html(text))
            plain_chunks = chunk_text(text)
            for i, chunk in enumerate(html_chunks):
                kwargs: dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                if i == 0 and markup:
                    kwargs["reply_markup"] = markup
                if edit_message_id and i == 0:
                    try:
                        await _flood_safe(
                            lambda kwargs=kwargs: context.bot.edit_message_text(
                                message_id=edit_message_id, **kwargs
                            )
                        )
                        continue
                    except RetryAfter:
                        raise
                    except Exception:
                        plain = {
                            **kwargs,
                            "text": plain_chunks[0],
                            "parse_mode": None,
                        }
                        try:
                            await _flood_safe(
                                lambda plain=plain: context.bot.edit_message_text(
                                    message_id=edit_message_id, **plain
                                )
                            )
                            continue
                        except RetryAfter:
                            raise
                        except Exception:
                            pass
                try:
                    await _flood_safe(lambda kwargs=kwargs: context.bot.send_message(**kwargs))
                except RetryAfter:
                    raise
                except Exception:
                    await _flood_safe(
                        lambda i=i, kwargs=kwargs: context.bot.send_message(
                            chat_id=chat_id,
                            text=plain_chunks[min(i, len(plain_chunks) - 1)],
                            disable_web_page_preview=True,
                            reply_markup=kwargs.get("reply_markup"),
                        )
                    )

        async def _send_for_hitl(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
            async def _inner(*, text: str, buttons: list[dict[str, str]] | None = None) -> None:
                await send_html(chat_id, text, buttons=buttons, context=context)

            return _inner

        async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.effective_user or not await _allowed(update.effective_user.id):
                return
            caption = f"{brand_label()} ready. Send a message or /profile."
            try:
                with open(logo_path(), "rb") as fh:
                    await update.message.reply_photo(photo=fh, caption=caption)
            except Exception:
                await _reply(update, caption)

        async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await _reply(update, "Commands: " + ", ".join(f"/{c}" for c, _ in COMMANDS))

        async def on_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            uid = update.effective_user.id if update.effective_user else 0
            ev = self._cancel.setdefault(uid, asyncio.Event())
            ev.set()
            n = self.hitl.cancel_all("cancel")
            # Cancel the running turn task too: the event is cooperative, but a
            # blocked tool needs the task cancel to unwind promptly.
            task = self._turns.get(uid)
            if task is not None and not task.done():
                task.cancel()
            else:
                self._busy.discard(uid)
            await _reply(
                update, f"Stop requested (cleared {n} pending approval(s)). Send a new message."
            )

        async def on_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            uid = str(update.effective_user.id)
            profile = await self.store.get_sticky_profile("telegram", uid) or "default"
            rows = await self.store.list_sessions(profile_id=profile, user_id=uid, limit=10)
            text = "\n".join(f"{r['id']} {r['updated_at']}" for r in rows) or "(none)"
            await _reply(update, text)

        async def on_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not context.args:
                await _reply(update, "usage: /resume <session_id>")
                return
            uid = update.effective_user.id
            self._sessions[uid] = context.args[0]
            await _reply(update, f"resumed {context.args[0]}")

        async def on_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            args = context.args or []
            if args and args[0].lower() == "remove":
                if len(args) < 2:
                    await _reply(update, "usage: /profile remove <id>")
                    return
                pid = args[1].strip()
                if pid == "default":
                    await _reply(update, "cannot remove the default profile")
                    return
                buttons = [
                    {"label": f"Remove {pid}", "data": f"profrmok:{pid}"},
                    {"label": "Cancel", "data": "profrmno"},
                ]
                await send_html(
                    update.effective_chat.id,
                    f"Remove profile `{pid}`? This deletes profiles/{pid}/.",
                    buttons=buttons,
                    context=context,
                )
                return
            profiles = list_profiles(self.settings.home)
            buttons = [{"label": p, "data": f"profile:{p}"} for p in profiles]
            await send_html(
                update.effective_chat.id,
                "Choose profile (starts a new session).\nOr `/profile remove <id>`.",
                buttons=buttons,
                context=context,
            )

        async def on_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.message or not update.effective_user:
                return
            from lattice.profiles import get_profile
            from lattice.providers.settings import normalize_primary_model_id, resolve_model_id

            uid = str(update.effective_user.id)
            args = context.args or []
            if args and args[0].lower() == "clear":
                await self.store.clear_sticky_primary_model("telegram", uid)
                await _reply(update, "primary model sticky cleared")
                return
            if args:
                try:
                    model_id = normalize_primary_model_id(" ".join(args))
                except ValueError as exc:
                    await _reply(update, str(exc))
                    return
                await self.store.set_sticky_primary_model("telegram", uid, model_id)
                await _reply(update, f"primary model → {model_id}")
                return
            sticky = await self.store.get_sticky_primary_model("telegram", uid)
            pid = await self.store.get_sticky_profile("telegram", uid) or "default"
            profile_model = None
            try:
                profile = get_profile(pid, self.settings.home)
                profile_model = profile.primary_model or profile.model
            except Exception:
                pass
            effective = resolve_model_id(
                self.settings, profile_model=profile_model, sticky_model=sticky
            )
            if sticky:
                await _reply(update, f"{effective} (sticky)")
            elif profile_model:
                await _reply(update, f"{effective} (profile {pid})")
            else:
                await _reply(update, f"{effective} (config)")

        async def on_tools(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await _reply(
                update,
                f"allow={self.settings.telegram.tools.allow} "
                f"deny={self.settings.telegram.tools.deny}",
            )

        async def on_forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await _reply(
                update,
                "Use the memory_forget tool in chat, or pass an id: /forget <id> (wired via turn).",
            )

        async def _profile_file_cmd(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            *,
            noun: str,
            read: Any,
            write: Any,
            reset: Any,
        ) -> None:
            if not update.message or not update.effective_user:
                return
            uid = str(update.effective_user.id)
            pid = await self.store.get_sticky_profile("telegram", uid) or "default"
            raw = (update.message.text or "").strip()
            # Keep the raw remainder (newlines included); context.args collapses them.
            split = raw.split(maxsplit=1)
            rest = split[1].strip() if len(split) > 1 else ""
            low = rest.lower()

            if not rest or low in {"show", "view", "get"}:
                current = read(pid, self.settings.home).strip() or "(empty)"
                if len(current) > 3500:
                    current = current[:3500] + "…"
                await _reply(update, f"{noun} for profile {pid}:\n\n{current}")
                return

            if low in {"reset", "default"}:
                reset(pid, self.settings.home)
                await _reply(update, f"{noun} reset to the built-in default for profile {pid}.")
                return

            for prefix in ("set ", "edit "):
                if low.startswith(prefix):
                    content = rest[len(prefix) :].lstrip("\n ").strip()
                    try:
                        write(pid, content, self.settings.home)
                    except (FileNotFoundError, ValueError) as exc:
                        await _reply(update, str(exc))
                        return
                    await _reply(
                        update, f"{noun} updated for profile {pid} — active on your next message."
                    )
                    return

            await _reply(
                update,
                f"usage:\n/{noun} — show current\n"
                f"/{noun} set <text> — replace it\n"
                f"/{noun} reset — restore the built-in default",
            )

        async def on_soul(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            from lattice.profiles import read_soul, reset_soul, write_soul

            await _profile_file_cmd(
                update,
                context,
                noun="soul",
                read=read_soul,
                write=write_soul,
                reset=reset_soul,
            )

        async def on_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            from lattice.profiles import read_soul_name, reset_soul_name, write_soul_name

            await _profile_file_cmd(
                update,
                context,
                noun="name",
                read=read_soul_name,
                write=write_soul_name,
                reset=reset_soul_name,
            )

        async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            query = update.callback_query
            if not query or not query.data:
                return
            data = query.data
            if data.startswith("hitl:"):
                _, token, value = data.split(":", 2)
                ok = self.hitl.resolve(token, value)
                logger.info("hitl callback token=%s value=%s ok=%s", token, value, ok)
                label = {
                    "approve": "Approved",
                    "deny": "Denied",
                    "cancel": "Cancelled",
                }.get(value, value)
                if value.startswith("c") and value[1:].isdigit():
                    try:
                        letter = chr(ord("A") + int(value[1:]))
                    except ValueError:
                        letter = value
                    label = f"Chose {letter}"
                with contextlib.suppress(Exception):
                    await query.answer()
                chat_id = update.effective_chat.id if update.effective_chat else None
                if ok and chat_id is not None:
                    with contextlib.suppress(Exception):
                        await _flood_safe(
                            lambda: context.bot.send_message(chat_id=chat_id, text=label)
                        )
                    with contextlib.suppress(Exception):
                        await _flood_safe(
                            lambda: query.edit_message_reply_markup(reply_markup=None)
                        )
                elif not ok and chat_id is not None:
                    with contextlib.suppress(Exception):
                        await _flood_safe(
                            lambda: context.bot.send_message(
                                chat_id=chat_id,
                                text="That approval expired — send a new message.",
                            )
                        )
                return
            await query.answer()
            if data == "profrmno":
                with contextlib.suppress(Exception):
                    await _edit(query, "remove cancelled")
                return
            if data.startswith("profrmok:"):
                pid = data.split(":", 1)[1]
                try:
                    remove_profile(pid, self.settings.home)
                    await self.store.clear_sticky_for_profile(pid)
                    uid = query.from_user.id if query.from_user else 0
                    sticky = await self.store.get_sticky_profile("telegram", str(uid))
                    if sticky == pid:
                        await self.store.set_sticky_profile("telegram", str(uid), "default")
                    self._sessions.pop(uid, None)
                    await _edit(query, f"removed profile {pid}")
                except (ValueError, FileNotFoundError) as exc:
                    await _edit(query, f"remove failed: {exc}")
                return
            if data.startswith("profile:"):
                profile_id = data.split(":", 1)[1]
                uid = str(query.from_user.id)
                await self.store.set_sticky_profile("telegram", uid, profile_id)
                self._sessions.pop(query.from_user.id, None)
                await _edit(query, f"profile → {profile_id} (new session)")

        async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.message or not update.effective_user:
                return
            if update.effective_chat and update.effective_chat.type != "private":
                return
            user = update.effective_user
            if not await _allowed(user.id):
                return
            update_id = update.update_id
            if update_id in self._seen_updates:
                return
            self._seen_updates.add(update_id)
            if len(self._seen_updates) > 5000:
                self._seen_updates = set(list(self._seen_updates)[-1000:])

            text = update.message.text or update.message.caption or ""
            media_paths: list[Path] = []
            dest = self.settings.home / "workspace" / "inbound"
            dest.mkdir(parents=True, exist_ok=True)
            if update.message.document:
                tg_file = await update.message.document.get_file()
                path = dest / (update.message.document.file_name or f"doc-{update_id}")
                await tg_file.download_to_drive(custom_path=str(path))
                media_paths.append(path)
            elif update.message.photo:
                # largest size last
                photo = update.message.photo[-1]
                tg_file = await photo.get_file()
                path = dest / f"photo-{update_id}.jpg"
                await tg_file.download_to_drive(custom_path=str(path))
                media_paths.append(path)

            uid = user.id
            cancel_event = self._cancel.setdefault(uid, asyncio.Event())
            cancel_event.clear()
            if uid in self._busy:
                # Free-text HITL clarify (e.g. timezone) must resolve the waiting turn,
                # not start a queued turn that never runs.
                if self.hitl.resolve_text(str(uid), text):
                    with contextlib.suppress(Exception):
                        await _reply(update, warm_confirmation("ok") or "ok")
                    return
                q = self._queues.setdefault(uid, asyncio.Queue(maxsize=self.settings.queue_depth))
                try:
                    q.put_nowait(text)
                    await _reply(update, "queued")
                except asyncio.QueueFull:
                    await _reply(update, "queue full")
                return

            async def _run_turn(turn_text: str, paths: list[Path]) -> None:
                chat_id = update.effective_chat.id

                async def _typing() -> None:
                    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

                await _typing()
                with contextlib.suppress(Exception):
                    await update.message.set_reaction("👀")
                opening = idle_phrase(0)
                status = await _reply(update, opening)
                sticky = await self.store.get_sticky_profile("telegram", str(uid)) or "default"
                self.hitl.set_active_user(str(uid))
                self.hitl.bind_send(await _send_for_hitl(context, chat_id))
                logger.info(
                    "recv user=%s profile=%s text=%s",
                    uid,
                    sticky,
                    (turn_text[:200] + "…") if len(turn_text) > 200 else turn_text,
                )

                class _TelegramStatusSink:
                    def __init__(self) -> None:
                        self._last = opening

                    async def set_status(self, text: str) -> None:
                        if text == self._last:
                            return
                        self._last = text
                        try:
                            await _flood_safe(
                                lambda: context.bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=status.message_id,
                                    text=markdown_to_telegram_html(text),
                                    parse_mode="HTML",
                                )
                            )
                        except Exception:
                            with contextlib.suppress(Exception):
                                await _flood_safe(
                                    lambda: context.bot.edit_message_text(
                                        chat_id=chat_id,
                                        message_id=status.message_id,
                                        text=text,
                                    )
                                )

                stop_typing = asyncio.Event()
                typing_task = asyncio.create_task(typing_keepalive(_typing, stop=stop_typing))
                live = LiveTurnEvents(_TelegramStatusSink())
                try:
                    async with bind_live_events(live):
                        await live.on_status("thinking")
                        try:
                            outbound = await self.handler(
                                Inbound(
                                    text=turn_text,
                                    profile_id=sticky,
                                    user_id=str(uid),
                                    channel="telegram",
                                    session_id=self._sessions.get(uid),
                                    media_paths=paths,
                                    cancel_event=cancel_event,
                                )
                            )
                        except asyncio.CancelledError:
                            outbound = Outbound(text="[cancelled]", profile_id=sticky)
                finally:
                    stop_typing.set()
                    typing_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await typing_task
                    self.hitl.set_active_user(None)
                logger.info("send user=%s chars=%d", uid, len(outbound.text or ""))
                if outbound.session_id:
                    self._sessions[uid] = outbound.session_id
                # Always deliver the final answer as a new message. Editing the
                # status bubble updates it in place, and Telegram sends no push
                # notification for edits, so the reply would arrive silently.
                # A bare "ok" deserves something warmer and non-repeating.
                reply_text = warm_confirmation(outbound.text) or outbound.text
                await send_html(
                    chat_id,
                    reply_text,
                    buttons=[{"label": b.label, "data": b.data} for b in outbound.buttons]
                    if outbound.buttons
                    else None,
                    context=context,
                )
                # Clear the transient thinking bubble now the real reply is sent.
                with contextlib.suppress(Exception):
                    await context.bot.delete_message(chat_id=chat_id, message_id=status.message_id)
                if outbound.media_paths:
                    for mp in outbound.media_paths:
                        suf = Path(mp).suffix.lower()
                        try:
                            if suf in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                                await context.bot.send_photo(chat_id=chat_id, photo=str(mp))
                            else:
                                await context.bot.send_document(chat_id=chat_id, document=str(mp))
                        except Exception:
                            logger.exception("failed sending media %s", mp)

            self._busy.add(uid)
            self._turns[uid] = asyncio.current_task()
            try:
                await _run_turn(text, media_paths)
                # Drain queued follow-ups as fresh turns.
                q = self._queues.get(uid)
                while q is not None and not q.empty():
                    nxt = q.get_nowait()
                    await _run_turn(nxt, [])
            finally:
                self._turns.pop(uid, None)
                self._busy.discard(uid)

        app.add_handler(CommandHandler("start", on_start))
        app.add_handler(CommandHandler("help", on_help))
        app.add_handler(CommandHandler("stop", on_stop))
        app.add_handler(CommandHandler("sessions", on_sessions))
        app.add_handler(CommandHandler("resume", on_resume))
        app.add_handler(CommandHandler("profile", on_profile))
        app.add_handler(CommandHandler("soul", on_soul))
        app.add_handler(CommandHandler("name", on_name))
        app.add_handler(CommandHandler("model", on_model))
        app.add_handler(CommandHandler("tools", on_tools))
        app.add_handler(CommandHandler("forget", on_forget))
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_message))

        # Application.run_polling() owns its own event loop and cannot nest under
        # asyncio.run() used by the Lattice CLI. Use the async lifecycle instead.
        await app.initialize()
        if app.post_init:
            await app.post_init(app)
        await app.start()
        assert app.updater is not None
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _request_stop(*_args: object) -> None:
            loop.call_soon_threadsafe(stop.set)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, _request_stop)
        try:
            await stop.wait()
        finally:
            with contextlib.suppress(Exception):
                await app.updater.stop()
            with contextlib.suppress(Exception):
                await app.stop()
            with contextlib.suppress(Exception):
                await app.shutdown()
