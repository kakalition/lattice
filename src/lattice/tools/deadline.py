"""Unified deadline helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def with_deadline[T](
    awaitable: Awaitable[T],
    *,
    seconds: float,
    label: str = "operation",
) -> T:
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except TimeoutError as exc:
        raise TimeoutError(f"{label} exceeded {seconds}s") from exc


async def run_with_deadline[T](
    fn: Callable[[], Awaitable[T]],
    *,
    seconds: float,
    label: str = "operation",
) -> T:
    return await with_deadline(fn(), seconds=seconds, label=label)
