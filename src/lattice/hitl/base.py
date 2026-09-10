"""HITL port protocol and request/response models."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ApprovalRequest(BaseModel):
    tool_name: str
    summary: str
    detail: str = ""
    risk: str = "high"


class ClarifyRequest(BaseModel):
    question: str
    choices: list[str] = Field(default_factory=list)


@runtime_checkable
class HitlPort(Protocol):
    async def approve(self, req: ApprovalRequest) -> ApprovalDecision: ...

    async def clarify(self, req: ClarifyRequest) -> str: ...


class AutoApproveHitl:
    """Non-interactive HITL used in tests / unattended deny-by-default scheduler."""

    def __init__(self, *, approve_all: bool = False) -> None:
        self.approve_all = approve_all

    async def approve(self, req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.APPROVE if self.approve_all else ApprovalDecision.DENY

    async def clarify(self, req: ClarifyRequest) -> str:
        if req.choices:
            return req.choices[0]
        return ""
