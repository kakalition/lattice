"""Memory port protocol."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Memory(Protocol):
    async def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]: ...

    async def add(
        self, text: str, *, metadata: dict[str, Any] | None = None, infer: bool = True
    ) -> str: ...

    async def update(self, memory_id: str, text: str) -> None: ...

    async def forget(self, memory_id: str) -> None: ...

    async def sync_turn(self, messages: list[dict[str, Any]]) -> None: ...
