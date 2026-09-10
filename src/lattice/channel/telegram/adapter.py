"""Telegram channel adapter wrapper."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from lattice.channel.telegram.bot import TelegramBot
from lattice.config import LatticeSettings
from lattice.hitl.telegram_adapter import TelegramHitlAdapter
from lattice.models import Inbound, Outbound
from lattice.session import SessionStore


class TelegramAdapter:
    def __init__(
        self,
        settings: LatticeSettings,
        *,
        hitl: TelegramHitlAdapter | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self.settings = settings
        self.hitl = hitl or TelegramHitlAdapter(timeout_seconds=settings.agent.hitl_timeout_seconds)
        self.store = store
        self._bot: TelegramBot | None = None

    def name(self) -> str:
        return "telegram"

    async def send(self, msg: Outbound) -> None:
        return None

    async def run(self, handler: Callable[[Inbound], Awaitable[Outbound]]) -> None:
        self._bot = TelegramBot(self.settings, handler, hitl=self.hitl, store=self.store)
        await self._bot.run(handler)
