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
