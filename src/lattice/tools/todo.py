"""In-session todo list."""

from __future__ import annotations

from pydantic import BaseModel, Field, PrivateAttr


class TodoItem(BaseModel):
    id: int
    text: str
    done: bool = False


class TodoList(BaseModel):
    items: list[TodoItem] = Field(default_factory=list)
    _next_id: int = PrivateAttr(default=1)

    @classmethod
    def from_items(cls, items: list[dict] | None) -> TodoList:
        """Restore a persisted list, including the next id.

        ``_next_id`` is a ``PrivateAttr`` and is *not* restored by validation, so
        it must be set explicitly or the next ``add`` reuses an existing id.
        """
        parsed = [TodoItem(**item) for item in (items or [])]
        todos = cls(items=parsed)
        todos._next_id = max((item.id for item in parsed), default=0) + 1
        return todos

    def add(self, text: str) -> TodoItem:
        item = TodoItem(id=self._next_id, text=text)
        self._next_id += 1
        self.items.append(item)
        return item

    def complete(self, item_id: int) -> TodoItem | None:
        for item in self.items:
            if item.id == item_id:
                item.done = True
                return item
        return None

    def render(self) -> str:
        if not self.items:
            return "(empty todo list)"
        lines = []
        for item in self.items:
            mark = "x" if item.done else " "
            lines.append(f"[{mark}] #{item.id} {item.text}")
        return "\n".join(lines)
