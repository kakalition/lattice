"""Send scheduler outbound messages to Telegram chats."""

from __future__ import annotations

import logging
from typing import Any

from lattice.channel.telegram.formatting import chunk_text, markdown_to_telegram_html
from lattice.config import LatticeSettings
from lattice.models import Outbound

logger = logging.getLogger("lattice.telegram.deliver")


async def deliver_telegram(settings: LatticeSettings, outbound: Outbound) -> None:
    token = settings.telegram.token
    if not token:
        logger.error("telegram deliver skipped: no token")
        return
    chat_ids = list(settings.telegram.allowlist)
    if not chat_ids:
        logger.error("telegram deliver skipped: empty allowlist")
        return

    from telegram import Bot

    bot = Bot(token)
    html = markdown_to_telegram_html(outbound.text)
    async with bot:
        for chat_id in chat_ids:
            for i, chunk in enumerate(chunk_text(html)):
                kwargs: dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                try:
                    await bot.send_message(**kwargs)
                except Exception:
                    logger.exception(
                        "telegram HTML send failed chat_id=%s; retrying plain", chat_id
                    )
                    plain = chunk_text(outbound.text)
                    await bot.send_message(
                        chat_id=chat_id,
                        text=plain[min(i, len(plain) - 1)],
                        disable_web_page_preview=True,
                    )
            logger.info("telegram delivered to chat_id=%s (%d chars)", chat_id, len(outbound.text))
