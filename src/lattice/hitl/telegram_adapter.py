"""Telegram HITL adapter — inline keyboard + free-text clarify replies."""

from __future__ import annotations

import asyncio
from typing import Any

from lattice.hitl.base import ApprovalDecision, ApprovalRequest, ClarifyRequest


class TelegramHitlAdapter:
    """Resolves approvals via pending futures keyed by short callback tokens.

    Free-text clarify answers are accepted while a turn is busy (the Telegram
    bot routes the next DM into ``resolve_text`` instead of queueing a new turn).
    """

    def __init__(self, *, timeout_seconds: int = 600, send_fn: Any = None) -> None:
        self.timeout_seconds = timeout_seconds
        self._send_fn = send_fn
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._text_waiters: dict[str, str] = {}  # user_id -> token
        self._active_user: str | None = None
        self._seq = 0

    def bind_send(self, send_fn: Any) -> None:
        self._send_fn = send_fn

    def set_active_user(self, user_id: str | None) -> None:
        self._active_user = user_id

    def resolve(self, token: str, value: str) -> None:
        fut = self._pending.get(token)
        if fut and not fut.done():
            fut.set_result(value)

    def resolve_text(self, user_id: str, text: str) -> bool:
        """If this user has a pending free-text clarify, resolve it. Returns True if consumed."""
        token = self._text_waiters.get(user_id)
        if not token:
            return False
        fut = self._pending.get(token)
        if not fut or fut.done():
            self._text_waiters.pop(user_id, None)
            return False
        fut.set_result(text)
        return True

    def awaiting_text(self, user_id: str) -> bool:
        return user_id in self._text_waiters

    def _token(self) -> str:
        self._seq += 1
        return f"h{self._seq}"

    async def approve(self, req: ApprovalRequest) -> ApprovalDecision:
        token = self._token()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending[token] = fut
        if self._send_fn:
            await self._send_fn(
                text=f"Approve `{req.tool_name}`?\n{req.summary}",
                buttons=[
                    {"label": "Approve", "data": f"hitl:{token}:approve"},
                    {"label": "Deny", "data": f"hitl:{token}:deny"},
                ],
            )
        try:
            decision = await asyncio.wait_for(fut, timeout=self.timeout_seconds)
        except TimeoutError:
            return ApprovalDecision.TIMEOUT
        finally:
            self._pending.pop(token, None)
        if decision == "approve":
            return ApprovalDecision.APPROVE
        if decision == "cancel":
            return ApprovalDecision.CANCELLED
        return ApprovalDecision.DENY

    async def clarify(self, req: ClarifyRequest) -> str:
        token = self._token()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending[token] = fut
        user = self._active_user
        if user:
            self._text_waiters[user] = token
        buttons = [
            {"label": c[:40], "data": f"hitl:{token}:c{i}"} for i, c in enumerate(req.choices)
        ]
        prompt = req.question
        if not buttons:
            prompt = f"{req.question}\n\n(reply with text)"
        if self._send_fn:
            await self._send_fn(text=prompt, buttons=buttons or None)
        try:
            value = await asyncio.wait_for(fut, timeout=self.timeout_seconds)
        except TimeoutError:
            return ""
        finally:
            self._pending.pop(token, None)
            if user:
                self._text_waiters.pop(user, None)
        if value.startswith("c") and req.choices:
            try:
                idx = int(value[1:])
                return req.choices[idx]
            except (ValueError, IndexError):
                return value
        return value
