"""Turn event callbacks for streaming and tool progress."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TurnEvents(Protocol):
    async def on_status(self, message: str) -> None: ...

    async def on_stream_delta(self, text: str) -> None: ...

    async def on_tool_start(self, name: str, args: dict[str, Any]) -> None: ...

    async def on_tool_end(self, name: str, result: str) -> None: ...


class NullTurnEvents:
    async def on_status(self, message: str) -> None:
        return None

    async def on_stream_delta(self, text: str) -> None:
        return None

    async def on_tool_start(self, name: str, args: dict[str, Any]) -> None:
        return None

    async def on_tool_end(self, name: str, result: str) -> None:
        return None
