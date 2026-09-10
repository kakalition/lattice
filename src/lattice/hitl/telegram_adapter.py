"""Telegram HITL adapter — inline keyboard decisions."""

from __future__ import annotations

import asyncio
from typing import Any

from lattice.hitl.base import ApprovalDecision, ApprovalRequest, ClarifyRequest


class TelegramHitlAdapter:
    """Resolves approvals via pending futures keyed by short callback tokens."""

    def __init__(self, *, timeout_seconds: int = 600, send_fn: Any = None) -> None:
        self.timeout_seconds = timeout_seconds
        self._send_fn = send_fn
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._seq = 0

    def bind_send(self, send_fn: Any) -> None:
        self._send_fn = send_fn

    def resolve(self, token: str, value: str) -> None:
        fut = self._pending.get(token)
        if fut and not fut.done():
            fut.set_result(value)

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
        buttons = [
            {"label": c[:40], "data": f"hitl:{token}:c{i}"} for i, c in enumerate(req.choices)
        ]
        if self._send_fn:
            await self._send_fn(text=req.question, buttons=buttons or None)
        try:
            value = await asyncio.wait_for(fut, timeout=self.timeout_seconds)
        except TimeoutError:
            return ""
        finally:
            self._pending.pop(token, None)
        if value.startswith("c") and req.choices:
            try:
                idx = int(value[1:])
                return req.choices[idx]
            except (ValueError, IndexError):
                return value
        return value
