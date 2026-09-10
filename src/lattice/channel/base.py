"""Channel protocol."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from lattice.models import Inbound, Outbound

MessageHandler = Callable[[Inbound], Awaitable[Outbound]]


@runtime_checkable
class Channel(Protocol):
    def name(self) -> str: ...

    async def run(self, handler: MessageHandler) -> None: ...

    async def send(self, msg: Outbound) -> None: ...
