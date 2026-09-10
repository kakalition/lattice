"""Clarify tool — awaits HitlPort."""

from __future__ import annotations

from lattice.hitl.base import ClarifyRequest, HitlPort


async def clarify(question: str, hitl: HitlPort, *, choices: list[str] | None = None) -> str:
    return await hitl.clarify(ClarifyRequest(question=question, choices=choices or []))
