"""CLI HITL adapter — prompt via rich console (TUI modal later)."""

from __future__ import annotations

import asyncio

from rich.console import Console
from rich.prompt import Confirm, Prompt

from lattice.hitl.base import (
    ApprovalDecision,
    ApprovalRequest,
    ClarifyRequest,
)


class CliHitlAdapter:
    def __init__(self, *, timeout_seconds: int = 600, console: Console | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.console = console or Console()

    async def approve(self, req: ApprovalRequest) -> ApprovalDecision:
        self.console.print(f"[bold yellow]HITL approve[/] {req.tool_name}: {req.summary}")
        if req.detail:
            self.console.print(req.detail)
        try:
            ok = await asyncio.wait_for(
                asyncio.to_thread(Confirm.ask, "Allow?", default=False),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            return ApprovalDecision.TIMEOUT
        except asyncio.CancelledError:
            return ApprovalDecision.CANCELLED
        return ApprovalDecision.APPROVE if ok else ApprovalDecision.DENY

    async def clarify(self, req: ClarifyRequest) -> str:
        self.console.print(f"[bold cyan]Clarify[/]: {req.question}")
        if req.choices:
            for i, choice in enumerate(req.choices, 1):
                self.console.print(f"  {i}. {choice}")
        try:
            answer = await asyncio.wait_for(
                asyncio.to_thread(Prompt.ask, "Your answer"),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            return ""
        return answer
