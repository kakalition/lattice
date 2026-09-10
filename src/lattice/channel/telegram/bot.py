"""Telegram bot runner — DM-only native UX."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from lattice.channel.telegram.commands import COMMANDS
from lattice.channel.telegram.formatting import chunk_text, markdown_to_telegram_html
from lattice.channel.telegram.keyboards import inline_keyboard
from lattice.config import LatticeSettings
from lattice.hitl.telegram_adapter import TelegramHitlAdapter
from lattice.models import Inbound, Outbound
from lattice.profiles import list_profiles
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
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )

        app = Application.builder().token(token).build()

        async def _post_init(application: Any) -> None:
            await application.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])

        app.post_init = _post_init

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
                        await context.bot.edit_message_text(message_id=edit_message_id, **kwargs)
                        continue
                    except Exception:
                        plain = {
                            **kwargs,
                            "text": plain_chunks[0],
                            "parse_mode": None,
                        }
                        try:
                            await context.bot.edit_message_text(
                                message_id=edit_message_id, **plain
                            )
                            continue
                        except Exception:
                            pass
                try:
                    await context.bot.send_message(**kwargs)
                except Exception:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=plain_chunks[min(i, len(plain_chunks) - 1)],
                        disable_web_page_preview=True,
                        reply_markup=kwargs.get("reply_markup"),
                    )

        async def _send_for_hitl(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
            async def _inner(*, text: str, buttons: list[dict[str, str]] | None = None) -> None:
                await send_html(chat_id, text, buttons=buttons, context=context)

            return _inner

        async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.effective_user or not await _allowed(update.effective_user.id):
                return
            await update.message.reply_text("Lattice ready. Send a message or /profile.")

        async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await update.message.reply_text("Commands: " + ", ".join(f"/{c}" for c, _ in COMMANDS))

        async def on_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            uid = update.effective_user.id if update.effective_user else 0
            ev = self._cancel.setdefault(uid, asyncio.Event())
            ev.set()
            await update.message.reply_text("Stop requested.")

        async def on_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            uid = str(update.effective_user.id)
            profile = await self.store.get_sticky_profile("telegram", uid) or "default"
            rows = await self.store.list_sessions(profile_id=profile, user_id=uid, limit=10)
            text = "\n".join(f"{r['id']} {r['updated_at']}" for r in rows) or "(none)"
            await update.message.reply_text(text)

        async def on_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not context.args:
                await update.message.reply_text("usage: /resume <session_id>")
                return
            uid = update.effective_user.id
            self._sessions[uid] = context.args[0]
            await update.message.reply_text(f"resumed {context.args[0]}")

        async def on_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            profiles = list_profiles(self.settings.home)
            buttons = [{"label": p, "data": f"profile:{p}"} for p in profiles]
            await send_html(
                update.effective_chat.id,
                "Choose profile (starts a new session):",
                buttons=buttons,
                context=context,
            )

        async def on_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await update.message.reply_text(self.settings.agent.primary_model)

        async def on_tools(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await update.message.reply_text(
                f"allow={self.settings.telegram.tools.allow} "
                f"deny={self.settings.telegram.tools.deny}"
            )

        async def on_forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await update.message.reply_text(
                "Use the memory_forget tool in chat, or pass an id: /forget <id> (wired via turn)."
            )

        async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            query = update.callback_query
            if not query or not query.data:
                return
            await query.answer()
            data = query.data
            if data.startswith("hitl:"):
                _, token, value = data.split(":", 2)
                self.hitl.resolve(token, value)
                return
            if data.startswith("profile:"):
                profile_id = data.split(":", 1)[1]
                uid = str(query.from_user.id)
                await self.store.set_sticky_profile("telegram", uid, profile_id)
                self._sessions.pop(query.from_user.id, None)
                await query.edit_message_text(f"profile → {profile_id} (new session)")

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
            # documents/photos saved under workspace when present
            if update.message.document:
                tg_file = await update.message.document.get_file()
                dest = self.settings.home / "workspace" / "inbound"
                dest.mkdir(parents=True, exist_ok=True)
                path = dest / (update.message.document.file_name or f"doc-{update_id}")
                await tg_file.download_to_drive(custom_path=str(path))
                media_paths.append(path)

            uid = user.id
            if uid in self._busy:
                # Free-text HITL clarify (e.g. timezone) must resolve the waiting turn,
                # not start a queued turn that never runs.
                if self.hitl.resolve_text(str(uid), text):
                    with contextlib.suppress(Exception):
                        await update.message.reply_text("ok")
                    return
                q = self._queues.setdefault(uid, asyncio.Queue(maxsize=self.settings.queue_depth))
                try:
                    q.put_nowait(text)
                    await update.message.reply_text("queued")
                except asyncio.QueueFull:
                    await update.message.reply_text("queue full")
                return

            async def _run_turn(turn_text: str, paths: list[Path]) -> None:
                await context.bot.send_chat_action(
                    chat_id=update.effective_chat.id, action="typing"
                )
                with contextlib.suppress(Exception):
                    await update.message.set_reaction("👀")
                status = await update.message.reply_text("thinking…")
                sticky = await self.store.get_sticky_profile("telegram", str(uid)) or "default"
                self.hitl.set_active_user(str(uid))
                self.hitl.bind_send(await _send_for_hitl(context, update.effective_chat.id))
                logger.info(
                    "recv user=%s profile=%s text=%s",
                    uid,
                    sticky,
                    (turn_text[:200] + "…") if len(turn_text) > 200 else turn_text,
                )
                try:
                    outbound = await self.handler(
                        Inbound(
                            text=turn_text,
                            profile_id=sticky,
                            user_id=str(uid),
                            channel="telegram",
                            session_id=self._sessions.get(uid),
                            media_paths=paths,
                        )
                    )
                finally:
                    self.hitl.set_active_user(None)
                logger.info("send user=%s chars=%d", uid, len(outbound.text or ""))
                if outbound.session_id:
                    self._sessions[uid] = outbound.session_id
                await send_html(
                    update.effective_chat.id,
                    outbound.text,
                    buttons=[{"label": b.label, "data": b.data} for b in outbound.buttons]
                    if outbound.buttons
                    else None,
                    edit_message_id=status.message_id,
                    context=context,
                )
                if outbound.media_paths:
                    for mp in outbound.media_paths:
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id, document=str(mp)
                        )

            self._busy.add(uid)
            try:
                await _run_turn(text, media_paths)
                # Drain queued follow-ups as fresh turns.
                q = self._queues.get(uid)
                while q is not None and not q.empty():
                    nxt = q.get_nowait()
                    await _run_turn(nxt, [])
            finally:
                self._busy.discard(uid)

        app.add_handler(CommandHandler("start", on_start))
        app.add_handler(CommandHandler("help", on_help))
        app.add_handler(CommandHandler("stop", on_stop))
        app.add_handler(CommandHandler("sessions", on_sessions))
        app.add_handler(CommandHandler("resume", on_resume))
        app.add_handler(CommandHandler("profile", on_profile))
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
